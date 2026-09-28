### Title
Fee sufficiency check in `SignableTransaction::new` omits the OP_RETURN output, so the committed fee rate can fall below the requested/minimum rate - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` adds an OP_RETURN output to `tx_outs` when `data` is provided, but computes the transaction's weight/vbytes — and therefore `needed_fee` and the minimum-relay-fee check — from a template transaction built only from `payments`. The OP_RETURN output (up to ~83 vbytes for 80 bytes of data) is never counted. The result mirrors the reported bug class: a "does the balance cover the committed amount" check that omits an amount/output added to the final object, so the real transaction commits to more weight than the fee was calculated for.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`:

1. The OP_RETURN output is pushed onto `tx_outs` at lines 193-202.
2. The weight/vbytes used for fee calculation are computed at line 204 as `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — `calculate_weight_vbytes` (lines 62-127) rebuilds a `Transaction` whose `output` list is derived only from `payments` (plus optional `change`). The OP_RETURN output is absent from this template.
3. `needed_fee = fee_per_vbyte * vbytes` (line 206), the `TooLowFee` minimum-relay check (lines 211-213), and the `NotEnoughFunds` check (`input_sat < payment_sat + needed_fee`, lines 215-221) all use this understated `vbytes`.
4. The change-output path (lines 224-235) calls `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))`, which also excludes the OP_RETURN output.

So the signed transaction is up to ~83 vbytes larger than what the fee was sized for: the effective fee rate is strictly below `fee_per_vbyte`, and a transaction crafted to barely pass the minimum-relay check can actually be under `DEFAULT_MIN_RELAY_TX_FEE` once the OP_RETURN is included — just as the gauge's total distribution could exceed the reserves that were checked.

### Impact Explanation
- The effective sat/vbyte of the produced transaction is lower than the caller-requested `fee_per_vbyte`, by up to ~80 vbytes worth of fee.
- Worse, the `TooLowFee` guard is computed against the understated vsize: a caller can specify a `fee_per_vbyte` that passes the check for the no-OP_RETURN size but produces a transaction whose actual fee rate is below the Bitcoin default minimum relay fee. Such a transaction is rejected by `send_raw_transaction`, so the multisig produces signatures for an unbroadcastable transaction — the inputs are effectively locked in a failed plan and the fee budgeting logic in `processor/src/networks/bitcoin.rs` (`needed_fee`, `make_signable_transaction`) is misinformed, since it relies on `SignableTransaction::needed_fee()`/`fee()`.
- In the opposite direction, `input_sat` must only cover `payment_sat + needed_fee`, so a transaction that "just" covers funds at the intended rate silently pays a lower rate, degrading confirmation reliability for time-sensitive processor sends.

This is reachable purely from public inputs: `data` is a caller-supplied byte vector (the processor passes `None`, but the wallet API accepts arbitrary ≤80-byte data), and `fee_per_vbyte` is caller-chosen.

### Likelihood Explanation
Any caller that attaches OP_RETURN data while requesting a fee rate at or near the minimum relay fee triggers an under-fee, potentially unrelayable transaction. Callers attaching data at modest fee rates get a silently degraded fee rate. Triggering requires only calling the public constructor with `data: Some(..)`; no malicious validator, key material, or internal access is needed. Severity is bounded because actual fee paid can exceed `needed_fee` (leftover becomes fee), which often still clears the relay minimum — hence Medium, not High.

### Recommendation
Include the OP_RETURN output in the template transaction used by `calculate_weight_vbytes` (e.g., pass the fully built `tx_outs`, or the serialized `data` length, into the weight calculation), so `needed_fee`, the `TooLowFee` check, and the `NotEnoughFunds` check are evaluated against the transaction actually being signed — i.e., ensure the committed total (all outputs) is what the reserve/sufficiency check measures.

### Proof of Concept
```rust
// networks/bitcoin context; assumes one ReceivedOutput `input` and a destination `addr`.
let data = vec![0u8; 80]; // max allowed, adds ~83 vbytes of output
// Request exactly the minimum relay fee for the *real* transaction size.
// needed_fee is computed without the OP_RETURN, so pick fee_per_vbyte = 1 sat/vB
// on a small tx: the TooLowFee check passes against the understated vbytes.
let tx = SignableTransaction::new(
    vec![input],
    &[(addr, 10_000)],
    None,
    Some(data),
    1, // sat/vbyte
).unwrap();
// tx.needed_fee() == vbytes_without_op_return * 1
// Actual tx is ~83 vbytes larger, so real feerate < 1 sat/vB,
// below DEFAULT_MIN_RELAY_TX_FEE despite the TooLowFee check passing.
assert!(tx.needed_fee() < tx.transaction().vsize() as u64 * 1);
// rpc.send_raw_transaction(&signed_tx) -> rejected: min relay fee not met
```