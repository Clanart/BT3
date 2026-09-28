### Title
`SignableTransaction` fee/weight estimation ignores the OP_RETURN data output, producing transactions paying less than the intended fee rate (potentially below minimum relay fee) — (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` appends an `OP_RETURN` output carrying up to 80 bytes of attacker-influenced `data`, but both weight/vbyte estimates call `calculate_weight_vbytes(tx_ins.len(), payments, ...)` using only `payments` — the data output is never included in the size estimate. The resulting `needed_fee`, the `TooLowFee` minimum-relay check, and the change computation are all based on a size up to ~90 vbytes smaller than the real transaction.

### Finding Description
In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150`), the OP_RETURN output is pushed into `tx_outs` at lines 194–202 *before* the fee is computed, yet the size estimate at line 204 uses `payments` rather than `tx_outs`:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

`calculate_weight_vbytes` (lines 62–127) builds the weight-estimation transaction from `payments` and an optional `change` only — there is no parameter for `data`, so an OP_RETURN output of up to ~91 vbytes (8-byte value + script len + `OP_RETURN` + push of 80 bytes) is simply omitted. The same omission occurs in the change-path estimate at lines 225–226. Consequences:

- `needed_fee = fee_per_vbyte * vbytes` (line 206) is undercharged by `fee_per_vbyte * ~91` sats.
- The `TooLowFee` guard (lines 211–213) validates the underestimated `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the *underestimated* `vbytes`. A transaction whose actual virtual size puts its real fee rate below 1 sat/vB will pass this check and be emitted.
- The change value at lines 228–233 deducts the undercharged `fee_with_change`, so change is inflated and `fee()` (lines 138–141) confirms the transaction pays exactly the underestimated fee.

The `data` bytes are reachable by an unprivileged party: they correspond to the `InInstruction`/metadata embedded in Serai-bound Bitcoin outputs (see `processor/src/networks/bitcoin.rs` `extract_serai_data` flow and `tests/full-stack` mint/burn data), i.e. public transaction data that flows into transactions the threshold group signs.

### Impact Explanation
The multisig produces and broadcasts transactions paying a lower fee rate than requested — in the worst case below the default minimum relay fee, so the transaction never propagates or confirms. Payments/change outputs created this way are stuck until a replacement is negotiated, and the reported `needed_fee()`/`fee()` values are systematically wrong whenever `data` is present. Because Bitcoin burns are settlement-critical, chronically underpriced burn transactions degrade the bridge's payout liveness.

### Likelihood Explanation
Any `SignableTransaction::new` call with `data: Some(..)` mis-estimates; there is no caller-side mitigation since the API computes the fee internally. Triggering the below-min-relay case only requires `fee_per_vbyte` near the minimum plus non-empty data. It does not require key compromise or malicious validators — just an ordinary transaction carrying an instruction payload.

### Recommendation
Pass the fully-built `tx_outs` (payments + OP_RETURN [+ change]) into `calculate_weight_vbytes`, or add the serialized size of the OP_RETURN output to the estimate. Recompute the `TooLowFee` check against the *actual* virtual size including the data output, and add a test asserting `needed_fee() == fee()` equals `fee_per_vbyte * tx.vsize()` when `data` is `Some`.

### Proof of Concept
```rust
// Against networks/bitcoin/src/wallet/send.rs
// 1. Build inputs covering exactly payments + needed_fee at fee_per_vbyte = 1.
// 2. Call SignableTransaction::new(inputs, &payments, Some(change), Some(vec![0u8; 80]), 1).
// 3. Observe: estimate used `payments` only (line 204), so the OP_RETURN
//    (~91 bytes) is excluded from `vbytes`.
// 4. `tx.vsize()` of the final Transaction exceeds `vbytes`; actual fee rate
//    = needed_fee / actual_vsize < 1 sat/vB, yet `TooLowFee` (lines 211-213)
//    did not fire because it compared against the underestimated `vbytes`.
// 5. fee() (line 138) == needed_fee confirms the transaction really pays the
//    under-estimated amount, so bitcoind rejects it from the mempool.
```

Relevant code: `SignableTransaction::new` at `networks/bitcoin/src/wallet/send.rs:150-256`, `calculate_weight_vbytes` at `networks/bitcoin/src/wallet/send.rs:62-127`, `fee()` at `networks/bitcoin/src/wallet/send.rs:138-141`.