### Title
`SignableTransaction::new` omits the OP_RETURN data output from fee/vbytes estimation, so the transaction's real fee rate is lower than the caller-requested `fee_per_vbyte` — ([File: networks/bitcoin/src/wallet/send.rs](https://github.com))

### Summary
The bug class from the report is "the party is charged an amount disproportionate to what is specified/computed." In `networks/bitcoin/src/wallet/send.rs`, `SignableTransaction::new` appends an OP_RETURN output carrying `data` to `tx_outs` (lines 194–202), but both fee-estimation calls to `Self::calculate_weight_vbytes` pass only `payments` and `change` — the `data` output is never included in the weight/vbytes model. The transaction that gets signed is therefore larger than the size used to compute `needed_fee`, so the realized fee rate is strictly below `fee_per_vbyte`, and can fall below the mempool minimum relay fee even though the `TooLowFee` check passed.

### Finding Description
`SignableTransaction::new` builds `tx_outs` including an OP_RETURN output when `data` is supplied:

```rust
// networks/bitcoin/src/wallet/send.rs:194-202
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}
```

Yet the size used for the fee is computed without the data output:

```rust
// networks/bitcoin/src/wallet/send.rs:204
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

`calculate_weight_vbytes` (lines 62–127) reconstructs a `Transaction` whose `output` list is `payments` plus an optional `change` — it has no parameter for a data output at all. The same omission occurs when re-estimating with change:

```rust
// networks/bitcoin/src/wallet/send.rs:225-227
let (weight_with_change, vbytes_with_change) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
```

Consequences:

- `needed_fee = fee_per_vbyte * vbytes` underestimates the fee needed to hit the intended rate by `fee_per_vbyte * Δvbytes`, where Δvbytes ≈ `4 * (9 + push_overhead + data.len()) / 4` weight-derived bytes — up to roughly 90 extra vbytes for the maximum 80 bytes of allowed data (checked at line 171).
- The `TooLowFee` check at line 211 compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the under-estimated `vbytes`, so a transaction can pass the check while its actual relay fee rate is below the minimum.
- When a change output is produced, its value is `input_sat - payment_sat - fee_with_change` (line 228), locking the actual fee to `needed_fee`; the extra size is never paid for. When change is dropped or absent, the leftover becomes fee anyway, so the effective rate remains mispredicted relative to the API's documented contract (`fee()` vs `needed_fee()` divergence is only partially documented, and the size-derived portion is not).

### Impact Explanation
Any caller (including the processor path via `make_signable_transaction`, which today passes `data: None`, but the API is public and designed for external use) that supplies `data` produces a transaction whose actual sat/vbyte rate is lower than the requested `fee_per_vbyte`. The result can be a transaction below mempool minimum relay fee — i.e., funds committed to `needed_fee` but the transaction is non-relayable/non-confirmable, so the signed inputs are effectively stuck (the "funds reported spendable but not actually spendable" analog), and the caller is charged a fee that does not correspond to the documented rate — the same over/under-charged-relative-to-specification class as the Sherlock finding.

### Likelihood Explanation
Reachable by any unprivileged caller of the public `SignableTransaction::new` API with `data: Some(..)` — no validator collusion or privileged access needed. The discrepancy is deterministic whenever `data` is non-empty; whether it crosses the minimum-relay threshold depends on how close `fee_per_vbyte` is to the minimum and the data length (up to ~90 unaccounted vbytes).

### Recommendation
Include the OP_RETURN output in the weight model: extend `calculate_weight_vbytes` to accept the `data` length (or a constructed `ScriptBuf::new_op_return`) and push a corresponding `TxOut` into the template transaction, for both the no-change and with-change estimations. Alternatively, build the fee-estimation template from the final `tx_outs` vector so the estimation can never diverge from the constructed transaction.

### Proof of Concept
Construct a `SignableTransaction` with `data = Some(vec![0u8; 80])`, a change address, and `fee_per_vbyte = 1` sat/vbyte (the minimum permitted by the `TooLowFee` check). The estimated `vbytes` excludes the OP_RETURN output (~90+ bytes ≈ ~90 vbytes heavier), so `needed_fee` and the change output are sized for a transaction ~90 vbytes smaller than the signed result. The final transaction's real fee rate ≈ `needed_fee / (vbytes + 90)` < 1 sat/vbyte < `DEFAULT_MIN_RELAY_TX_FEE`, despite `needed_fee()` and the `TooLowFee` check asserting adequacy — the payer is charged `needed_fee` for a rate that was never achieved, and the transaction will be rejected by standard relay.