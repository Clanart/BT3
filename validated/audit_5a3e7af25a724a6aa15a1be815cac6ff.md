### Title
Fee computed before the full output set is known, omitting the OP_RETURN output weight and producing transactions with an insufficient fee rate - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` calculates `needed_fee` via `calculate_weight_vbytes`, which builds the weight-estimation transaction only from `payments` (and optionally `change`). The `data` OP_RETURN output, pushed onto `tx_outs` before the weight is computed, is never passed into `calculate_weight_vbytes`, so the serialized transaction is larger than what the fee was priced for.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is added to `tx_outs` at lines 194–202 of `networks/bitcoin/src/wallet/send.rs`, but the fee calculation at line 204 calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — which reconstructs a transaction containing only `payments` outputs. `calculate_weight_vbytes` (lines 62–127) has no parameter for data outputs at all. The change-fee calculation at line 226 likewise omits the OP_RETURN output. Consequently:

- `needed_fee = fee_per_vbyte * vbytes` is priced on a vbyte count that excludes up to ~80 bytes of OP_RETURN data plus output overhead (~20+ vbytes).
- The actual transaction (`self.tx.output = tx_outs`) does include the OP_RETURN output, so its real vsize exceeds `vbytes`/`vbytes_with_change`, and the effective fee rate is `needed_fee / real_vsize < fee_per_vbyte`.
- The `TooLowFee` check at line 211 validates `needed_fee` against the minimum relay fee using the understated `vbytes`; a transaction priced at exactly `fee_per_vbyte = 1 sat/vB` can fall below `DEFAULT_MIN_RELAY_TX_FEE` once the real size is counted.
- The `TooLargeTransaction` weight check at line 241 also uses a weight that excludes the OP_RETURN output, slightly understating the real weight.

This is the same bug class as the reference report: a fee is computed/deducted before the true final amount (here, the true final transaction size/output set) is known, producing a transaction that fails on-chain validation.

### Impact Explanation
An unprivileged caller supplies arbitrary `data` bytes (public input) to `SignableTransaction::new`. A transaction built with `data` set, signed, and broadcast will have an effective fee rate lower than requested — and can fall below the network minimum relay fee, causing the transaction to be rejected and the funds unspendable via this transaction. Additionally, the change amount is computed as `input_sat - payment_sat - fee_with_change`; since `fee_with_change` does not pay for the OP_RETURN size, the change output absorbs the shortfall in feerate rather than an explicit, correctly-sized fee — the transaction silently pays less than the caller-specified rate.

### Likelihood Explanation
Any caller passing `data` (an intended feature per the doc comment at line 149) triggers the miscalculation on every such transaction. Whether it causes outright rejection depends on how close `fee_per_vbyte` is to the relay minimum; at minimum-fee rates the OP_RETURN overhead alone can push the effective rate below the relay threshold. Even when it relays, the transaction pays a lower rate than requested, degrading confirmation guarantees.

### Recommendation
Pass the OP_RETURN output (or its size) into `calculate_weight_vbytes` — e.g., extend it to accept the complete set of `TxOut`s including the data output — so `needed_fee`, the change computation, the `TooLowFee` check, and the `MAX_STANDARD_TX_WEIGHT` check are all evaluated against the true final transaction. Alternatively, compute weight directly on the fully assembled `tx_outs` after the change decision, then re-derive the change value from that final fee.

### Proof of Concept
```rust
// networks/bitcoin crate; construct a SignableTransaction whose real vsize
// exceeds the vbytes used to price the fee.
let data = vec![0u8; 80]; // max allowed
let tx = SignableTransaction::new(
  vec![input],                    // any ReceivedOutput with sufficient value
  &[(payment_script, 10_000)],
  Some(change_script),
  Some(data),
  1,                              // fee_per_vbyte: exactly at typical min relay rate
).unwrap();

// The priced fee was computed on a tx without the OP_RETURN output:
assert_eq!(tx.needed_fee(), 1 * vbytes_without_op_return);
// The real transaction is larger:
assert!(tx.transaction().vsize() as u64 > vbytes_without_op_return);
// Effective fee rate = needed_fee / real_vsize < 1 sat/vB,
// so the tx can be rejected as below DEFAULT_MIN_RELAY_TX_FEE,
// while SignableTransaction::new never returned TooLowFee.
```
The discrepancy follows directly from `tx_outs` gaining an OP_RETURN at `send.rs:194-202` while `calculate_weight_vbytes` (`send.rs:62-127`) is invoked only with `payments`/`change` at `send.rs:204` and `send.rs:226`, leaving the OP_RETURN output unpriced.