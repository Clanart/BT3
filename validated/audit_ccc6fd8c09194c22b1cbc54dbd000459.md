### Title
`SignableTransaction::new` omits the OP_RETURN `data` output from the fee/weight calculation, producing transactions that underpay the requested fee rate and may fail relay — (File: networks/bitcoin/src/wallet/send.rs)

### Summary
When constructing a `SignableTransaction` with `data`, the OP_RETURN output is pushed onto `tx_outs` before the transaction weight/vbytes are estimated, but `calculate_weight_vbytes` is only given `payments` (and optionally `change`). The `data` output's ~10–90+ vbytes are never accounted for, so `needed_fee` and the minimum-relay-fee check are computed against a smaller virtual size than the final transaction. The signed transaction silently pays a lower effective fee rate than requested and can fall below `DEFAULT_MIN_RELAY_TX_FEE`, causing Bitcoin nodes to reject it.

### Finding Description
In `SignableTransaction::new` (networks/bitcoin/src/wallet/send.rs:194-204), the OP_RETURN output carrying `data` (up to 80 bytes, checked at line 171) is appended to `tx_outs` at lines 194-202. However, the vbyte estimate at line 204 calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)`, which builds a template `Transaction` whose `output` list contains only `payments` (lines 85-93) — the OP_RETURN output is absent. The same omission occurs for the change-inclusive estimate at lines 225-227. Consequently:

1. `needed_fee = fee_per_vbyte * vbytes` (line 206) underpays by `fee_per_vbyte * data_output_vbytes` (up to ~91 vbytes for 80 bytes of data).
2. The `TooLowFee` check at line 211 compares `needed_fee` against the minimum relay fee scaled by the same underestimated `vbytes`, so a transaction whose *actual* feerate falls below 1 sat/vB still passes.
3. The `MAX_STANDARD_TX_WEIGHT` check at line 241 uses the underestimated `weight`, so a data-carrying transaction that actually exceeds the standard weight limit is also accepted.

The actual fee paid is `sum(inputs) - sum(outputs)` (`fee()`, line 138), which equals the computed `needed_fee` — i.e., the fee that was calculated for a smaller transaction — because the change output absorbs exactly `input_sat - payment_sat - fee_with_change`.

### Impact Explanation
An unprivileged party who causes Serai to produce a transaction embedding `data` (e.g., by submitting a Bitcoin transaction carrying an InInstruction payload in an OP_RETURN/witness — the data flow in `extract_serai_data`) can force the coordinator's `SignableTransaction::new` to be invoked with up to 80 bytes of attacker-controlled data. The resulting signed transaction underpays its fee by up to ~91 vbytes × `fee_per_vbyte`, and at low fee rates can be under the mempool minimum relay fee, so it is rejected/never confirmed. The multisig's inputs are then committed to a transaction that won't propagate — funds are locked in an unbroadcastable/unconfirmable transaction until manually reconstructed, and the protocol emits a signed spend that does not achieve its stated fee policy. This is a reachable integrity failure of the signing path: the signers sign a transaction that does not satisfy the fee policy the API contract (`fee_per_vbyte`, `needed_fee()`, `TooLowFee`) promises.

### Likelihood Explanation
Reachable with public inputs: any user can attach arbitrary data to a Serai-bound Bitcoin transaction, and `data` flows into `SignableTransaction::new`. The miscalculation is deterministic — it occurs on every construction with `Some(data)` — with probability 1, not dependent on adversarial timing. Severity is Medium: it yields stuck/unrelayable spends rather than direct theft, and recovery requires re-constructing and re-signing the transaction.

### Recommendation
Include the OP_RETURN output in the weight/vbyte estimate: pass the data-bearing `TxOut` (or `tx_outs` as constructed so far) into `calculate_weight_vbytes`, both for the base estimate at line 204 and the with-change estimate at line 226. Alternatively, build the template transaction directly from `tx_outs` so all outputs (payments, OP_RETURN, change) are counted for `needed_fee`, the `TooLowFee` check, and the `MAX_STANDARD_TX_WEIGHT` check.

### Proof of Concept
```rust
// networks/bitcoin context; demonstrate the vbyte mismatch
let inputs = vec![received_output];            // any scanned ReceivedOutput
let payments = vec![(payment_script, 100_000u64)];
let data = vec![0xAA; 80];                      // max allowed by the len() > 80 check

let tx = SignableTransaction::new(inputs, &payments, None, Some(data), fee_rate).unwrap();
let actual_vbytes = tx.transaction().vsize() as u64;

// needed_fee was computed without the OP_RETURN output:
assert!(tx.needed_fee() < fee_rate * actual_vbytes);
// The shortfall equals roughly 8 + 1 + 1 + 80 ≈ 90 vbytes * fee_rate.
// With fee_rate == DEFAULT_MIN_RELAY_TX_FEE rate, tx.transaction().vsize() makes the
// effective feerate < 1 sat/vB, so a Bitcoin Core node rejects it as "min relay fee not met".
```
The defect is structural at send.rs:194-206: `tx_outs` gains the OP_RETURN `TxOut`, while the estimate at line 204 rebuilds outputs solely from `payments`, so `vbytes`/`weight` never see the data output.