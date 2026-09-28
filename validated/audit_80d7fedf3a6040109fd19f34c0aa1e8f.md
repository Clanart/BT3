### Title
`SignableTransaction::new` excludes the OP_RETURN output from the transaction's weight/vbytes, undercharging the fee and skipping the max-weight check - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the vault fee being assessed on the wrong asset base, `SignableTransaction::new` computes the transaction's weight, virtual size, and required fee over `payments` only — while the actual transaction being signed includes an additional OP_RETURN output built from caller-supplied `data`. The fee is therefore assessed against a smaller base than what is actually broadcast, and the standardness weight check is evaluated on the wrong transaction.

### Finding Description
`SignableTransaction::new` pushes an OP_RETURN `TxOut` into `tx_outs` when `data` is `Some` (lines 193–202). However, both calls to `Self::calculate_weight_vbytes` pass `payments` — which never includes the OP_RETURN output:

- Line 204: `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — used for `needed_fee`, the `NotEnoughFunds` check, the `TooLowFee` check, and `weight`.
- Line 226: `Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` — used for `fee_with_change`, which determines the change output's value (line 228: `input_sat.checked_sub(payment_sat + fee_with_change)`).

`calculate_weight_vbytes` builds a template `Transaction` whose `output` is derived solely from the `payments` argument (lines 85–94) plus an optional change output (lines 95–99). There is no parameter for the OP_RETURN output, so its weight (up to ~90 vbytes for the 80-byte `data` limit enforced at line 171) is never counted.

Consequences:

1. `needed_fee` and `fee_with_change` are computed on `vbytes` smaller than the real transaction's, so the actual fee rate (`fee() / actual_vbytes`) is strictly below `fee_per_vbyte` whenever `data` is present.
2. The change output value is inflated by the unaccounted fee difference, so the transaction pays less fee than `needed_fee()` reports — `fee()` (line 138) does correctly measure `sum(prevouts) - sum(outputs)`, so the discrepancy is real, not just reporting.
3. The `TooLowFee` check (line 211) and the `MAX_STANDARD_TX_WEIGHT` check (line 241) are both evaluated on the underestimated `weight`/`vbytes`, so a transaction can be produced that is below the minimum relay fee or over the standard weight limit once the OP_RETURN output is included.

### Impact Explanation
Any caller that supplies `data` produces a signed transaction paying a lower effective fee rate than requested, and potentially a nonstandard transaction (over `MAX_STANDARD_TX_WEIGHT` or under the minimum relay fee) that Bitcoin nodes will refuse to relay or mine. For a multisig wallet this means a signed spend can be unbroadcastable/stuck, and the change calculation is wrong in the user's favor at the expense of the fee, defeating the fee-rate parameter's intent. Like the Ribbon issue, a fee is assessed against a base that omits a component of what is actually being charged over.

### Likelihood Explanation
The bug triggers deterministically whenever `data.is_some()` — no race or adversarial ordering needed. Reachability depends on a caller supplying OP_RETURN data to `SignableTransaction::new` (the in-tree processor currently passes `None`, but the API accepts arbitrary transaction data to be embedded in the signed transaction). Severity is limited to incorrect fees / potentially unrelayable transactions rather than direct theft.

### Recommendation
Pass the OP_RETURN output (or the fully-built `tx_outs`/`Transaction`) into `calculate_weight_vbytes` so `weight`, `vbytes`, `needed_fee`, `fee_with_change`, and the max-weight check all reflect the transaction actually constructed. E.g., compute weight from `tx_outs` after the OP_RETURN push, and again after appending the change output.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs
// Construct a transaction with `data = Some(vec![0u8; 80])`.
// Inside SignableTransaction::new:
//   tx_outs gets 1 payment + 1 OP_RETURN output (lines 188-202)
//   calculate_weight_vbytes(tx_ins.len(), payments, None) builds a template
//   with ONLY the payment output (lines 85-94) — the OP_RETURN is absent.
// Therefore:
//   actual_vbytes = vbytes + OP_RETURN_size  (> vbytes by ~90+ for 80-byte data)
//   actual_fee_rate = fee() / actual_vbytes < fee_per_vbyte
// and a payments-only `weight` just under MAX_STANDARD_TX_WEIGHT produces a
// real transaction exceeding the standardness limit once OP_RETURN is added.
```