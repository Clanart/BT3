### Title
OP_RETURN `data` output excluded from fee/weight estimation, so `needed_fee` and the minimum-relay-fee check are computed on a transaction smaller than the one actually signed - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The fee-on-transfer class bug — "accounting performed on the stated/expected parameters rather than on what is actually being transacted" — has a direct analog in `SignableTransaction::new`. The weight/vsize used for `needed_fee` and for the `TooLowFee` (minimum relay fee) check is computed from `payments` only, after the OP_RETURN `data` output has already been appended to `tx_outs`. The transaction that is actually constructed and signed is larger than the one the fee was calculated for, so the effective fee rate is strictly lower than `fee_per_vbyte` and can fall below `DEFAULT_MIN_RELAY_TX_FEE` even though the check passed.

### Finding Description
`SignableTransaction::new` builds `tx_outs` from `payments`, then pushes the OP_RETURN output carrying caller-supplied `data` (up to 80 bytes) into `tx_outs` at `send.rs:194-202`. Immediately after, it estimates the transaction's weight via `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at `send.rs:204`, which reconstructs a mock transaction from `payments` — i.e., without the OP_RETURN output (see `calculate_weight_vbytes` at `send.rs:62-127`). `needed_fee` is then set to `fee_per_vbyte * vbytes` and validated against `DEFAULT_MIN_RELAY_TX_FEE` at `send.rs:206-213`, and the change-amount computation at `send.rs:225-234` reuses the same under-counted weight (`fee_with_change` also excludes the data output). The final `tx` stored in `SignableTransaction` uses `tx_outs`, which does include the OP_RETURN output (`send.rs:245-255`). The signature produced by `TransactionSignMachine::sign` binds to this real, larger transaction (`send.rs:373-390`), so the mismatch is baked into the signed result.

### Impact Explanation
Any transaction created with non-empty `data` pays a fee calculated for a smaller transaction than the one broadcast. The real transaction's fee rate is `needed_fee / actual_vsize`, which is below `fee_per_vbyte` by up to ~90 vbytes' worth of fee. When the fee rate is at or near the minimum relay boundary, the `TooLowFee` guard — which exists precisely to prevent this — passes on the underestimated size while the actual signed transaction is below `DEFAULT_MIN_RELAY_TX_FEE` and will not be relayed/confirmed. Funds spent by such a transaction are effectively locked: the protocol has produced and threshold-signed a transaction that cannot enter the mempool, matching the "funds reported moved that are not actually spendable" acceptance criterion. With a change output present, the error is not even compensated by overpayment — the shortfall silently lands in the change amount.

### Likelihood Explanation
The `data` argument is untrusted transaction-level input: it is the channel used to embed instructions alongside payments, and any transaction carrying it deterministically triggers the miscalculation — no race or special conditions needed. The only requirement for security impact is that the selected `fee_per_vbyte` be close enough to the relay minimum that a ~80–90 vbyte underestimate pushes the real rate below it. Every use of `SignableTransaction::new` with `Some(data)` produces an incorrectly priced transaction; whether it gets stuck depends on the margin between `fee_per_vbyte` and the relay minimum.

### Recommendation
Include the OP_RETURN output in the weight estimate: build the mock transaction inside `calculate_weight_vbytes` from `tx_outs` (or pass `data`/`tx_outs` through) rather than from `payments` alone, for both the no-change and with-change fee computations. Additionally, after constructing the final `tx`, recompute the actual fee rate (`sum(inputs) - sum(outputs)` over `tx.vsize()`) and enforce the minimum relay fee on the real transaction, not on the estimate — i.e., measure what is actually signed rather than what was passed in.

### Proof of Concept
```rust
// Conceptual, against networks/bitcoin/src/wallet/send.rs
// SignableTransaction::new(inputs, payments, change, Some(data), fee_per_vbyte)

// 1) data output is appended to tx_outs (send.rs:194-202)
// 2) but weight is estimated from `payments` only (send.rs:204):
//    calculate_weight_vbytes builds `tx.output` from `payments`,
//    never including the OP_RETURN output (send.rs:85-99).

// Concrete divergence:
//   let data = vec![0u8; 80]; // max allowed, ~90-byte output
//   fee_per_vbyte = 1 sat/vB at the relay-minimum boundary
//
// needed_fee passes the check:
//   needed_fee = fee_per_vbyte * vbytes(without OP_RETURN)
//              >= DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000      // passes
//
// Real transaction:
//   actual_vsize = vbytes + ~90 (OP_RETURN output bytes)
//   actual fee paid = needed_fee (unchanged, or change absorbs it)
//   effective rate = needed_fee / actual_vsize
//                  < DEFAULT_MIN_RELAY_TX_FEE per vbyte
//
// Result: TransactionSignMachine::sign produces a fully-signed TX
// that the network refuses to relay -> inputs locked in a tx
// that can never confirm, despite TooLowFee never firing.
```