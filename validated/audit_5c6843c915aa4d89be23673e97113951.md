### Title
`SignableTransaction::new` excludes the OP_RETURN output from the fee/weight calculation, producing transactions with an underpaid fee that may be unrelayable — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the L1ECOBridge issue where funds are committed before a step that silently fails due to underpaid gas, `SignableTransaction::new` computes `needed_fee` and the minimum-relay-fee check from a weight estimate that omits the OP_RETURN `data` output. The signed transaction is therefore larger than estimated and pays a lower effective fee rate than the caller specified, potentially below Bitcoin's default minimum relay fee, so the transaction cannot propagate and the multisig's funds are stuck in an unusable signed transaction.

### Finding Description
`SignableTransaction::new` builds `tx_outs`, appends an OP_RETURN output when `data` is provided (lines 194–202), but then calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at line 204 — passing `payments`, not the actual output list. `calculate_weight_vbytes` (lines 62–127) constructs a dummy `Transaction` whose outputs are only `payments` plus optional `change`; the OP_RETURN output is never accounted for. Consequently:

- `needed_fee = fee_per_vbyte * vbytes` (line 206) underestimates the true required fee by the serialized size of the OP_RETURN output (roughly `8 + 1 + (1 + data_len)` vbytes, up to ~92 vbytes for the allowed 80-byte payload).
- The `TooLowFee` check at line 211 compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the same underestimated `vbytes`, so it passes even though the real fee rate will be lower.
- The same omission occurs in the change branch: `fee_with_change` at lines 225–227 also uses `payments` rather than `tx_outs`.

The actual fee paid is `sum(inputs) - sum(outputs)` (see `fee()`, lines 138–141), which does not increase to compensate — the transaction simply has a larger real vsize than the estimate.

### Impact Explanation
When `data` is attached, the finalized transaction's effective fee rate is `needed_fee / actual_vsize`, strictly less than the requested `fee_per_vbyte`. If `fee_per_vbyte` is at or near the minimum relay fee (the case the `TooLowFee` check is designed to police), the broadcast transaction falls below `DEFAULT_MIN_RELAY_TX_FEE` and is rejected by node relay policy. The FROST multisig has then produced a signature over an unpropagatable transaction — the inputs appear spent by the signed TX yet cannot confirm — a loss-of-funds-availability condition directly mirroring the reference bug where tokens are taken and the dependent operation fails due to underpayment. Recovery requires reconstructing and re-signing a new transaction, which may not be possible if preprocesses/nonces were consumed.

### Likelihood Explanation
Any caller passing `Some(data)` to `SignableTransaction::new` with a low-but-acceptable `fee_per_vbyte` triggers the underestimate; the gap scales with `data.len()` up to 80 bytes. It does not require an attacker — only normal use of the documented `data` parameter — though an external party cannot force it.

### Recommendation
Include the OP_RETURN output in the weight/vbytes estimate: build the estimate from the final `tx_outs` list (payments + OP_RETURN + optional change) rather than from `payments`, or pass a `data_len` parameter into `calculate_weight_vbytes` so the output is sized correctly. Re-check `TooLowFee` against the corrected vbytes.

### Proof of Concept
```rust
// In networks/bitcoin, with an input `output` worth e.g. 10_000 sats:
let payments = [(p2tr_script_buf(key).unwrap(), 5_000u64)];
let data = Some(vec![0xaa; 80]); // maximal OP_RETURN payload
let fee_per_vbyte = 1; // 1 sat/vB, at the minimum relay boundary

let tx = SignableTransaction::new(
    vec![output], &payments, None, data, fee_per_vbyte,
).unwrap();

// tx_outs = payment + OP_RETURN, but needed_fee was computed for payment only.
// Actual vsize exceeds the vbytes used in the fee calc by ~92 bytes.
assert!(tx.needed_fee() < tx.transaction().vsize() as u64 * fee_per_vbyte);
// Effective fee rate < 1 sat/vB -> below DEFAULT_MIN_RELAY_TX_FEE -> unrelayable.
```
The discrepancy is visible directly in `send.rs`: the OP_RETURN is pushed at lines 194–202 while the weight estimate at line 204 uses only `payments`.