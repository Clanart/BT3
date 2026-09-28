### Title
`OP_RETURN` data output excluded from transaction weight/fee calculation, producing an underpriced or oversized transaction that cannot be relayed - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
`SignableTransaction::new` builds the final `tx_outs` including an optional `OP_RETURN` data output, but then computes the transaction's weight, vbytes, and required fee using only `payments` — which never contains the data output. The result is a signed transaction whose actual size is larger than accounted for, so its effective fee rate is lower than requested (potentially below the minimum relay fee) and its actual weight may exceed `MAX_STANDARD_TX_WEIGHT` even though the check passed.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, the `OP_RETURN` output is appended to `tx_outs` at lines 194-202, *before* the weight/vbytes computation. However, `calculate_weight_vbytes(tx_ins.len(), payments, None)` at line 204 is invoked with `payments`, not `tx_outs`, so the data output contributes nothing to `weight` or `vbytes`. The same exclusion applies to the with-change recalculation at lines 225-226. Consequently:

- `needed_fee = fee_per_vbyte * vbytes` (line 206) underpays by `fee_per_vbyte * <vbytes of the OP_RETURN output>` — up to ~90 vbytes (8-byte amount + script length + up to 82 bytes of script) since `data` may be 80 bytes (checked at line 171).
- The `TooLowFee` check at line 211 validates the underpriced `needed_fee` against the underestimated `vbytes`, so it can pass while the real fee rate falls below `DEFAULT_MIN_RELAY_TX_FEE`.
- The `MAX_STANDARD_TX_WEIGHT` check at line 241 uses the underestimated `weight`, so a transaction near the standardness limit can pass the check while its true weight exceeds it.

This is the Serai analog of "insufficient gas limit causes irreversible message failure": a caller who supplies `data` (public input) obtains a `SignableTransaction`, drives FROST signing via `TransactionMachine::sign`/`complete`, and ends with a fully-signed transaction that Bitcoin nodes will reject or refuse to relay. The signature binds to the exact transaction (sighash `Prevouts::All`), so the fee cannot be raised on the signed transaction — the entire signing round must be discarded and re-run, and any protocol bookkeeping that treated the plan as completed (e.g., `needed_fee`/txid commitments, `created_output` accounting) is invalidated.

### Impact Explanation
A transaction created with a `data` payload is silently assigned an insufficient fee relative to its true size and may exceed the standard weight limit despite passing the internal check. The signed transaction is unbroadcastable/irreversibly failed at the network layer, mirroring the L1→L2 message that fails due to insufficient gas: the operation cannot be remedied once produced because the signature commits to the underpriced transaction. All inputs are consumed by an unusable TXID, and any downstream `Eventuality`/plan tracking bound to `txid()` will never be fulfilled.

### Likelihood Explanation
`data` is an arbitrary up-to-80-byte public input to `SignableTransaction::new`, an unprivileged-reachable constructor. Any invocation that specifies `data` triggers the miscalculation deterministically; the failure manifests whenever the true fee rate falls under the relay minimum or the true weight exceeds the standard limit — most likely precisely when many inputs/payments push the transaction near the bounds, i.e., when the checks matter most. The processor currently passes `None` for `data`, so the defect primarily affects direct consumers of `bitcoin-serai`'s wallet API.

### Recommendation
Include the serialized `OP_RETURN` output in `calculate_weight_vbytes` — e.g., build the complete output list (payments + data + optional change) first and pass it to the weight function, or add the data output's serialized size to `weight`/`vbytes` before computing `needed_fee`, the `TooLowFee` check, the change calculation, and the `MAX_STANDARD_TX_WEIGHT` check. Alternatively, recompute `tx.weight()`/`vsize` on the fully assembled `Transaction` once `tx_outs` is final.

### Proof of Concept
```rust
// networks/bitcoin conceptual PoC
let data = vec![0u8; 80]; // max allowed
let st = SignableTransaction::new(
  inputs,                       // enough sats
  &payments,                    // script/amount pairs
  Some(change_script),
  Some(data.clone()),           // OP_RETURN output
  fee_per_vbyte,
).unwrap();

let tx = st.transaction().clone();
// The internal accounting ignores the OP_RETURN output:
let accounted_vbytes = st.needed_fee() / fee_per_vbyte;
let actual_vbytes = tx.vsize() as u64; // after witness, approx
assert!(actual_vbytes > accounted_vbytes); // ~90 vbyte discrepancy

// Real fee rate is below the requested rate and may be below
// DEFAULT_MIN_RELAY_TX_FEE, or weight may exceed
// MAX_STANDARD_TX_WEIGHT while the TooLargeTransaction check passed.
// The FROST signature over this tx cannot be repaired -> failed, irrecoverable message.
```

Relevant code: `networks/bitcoin/src/wallet/send.rs` lines 194-204 (data output added but weight computed from `payments`), lines 206-213 (fee/min-relay check on underestimated size), lines 224-235 (change recalc with same omission), line 241 (weight bound check).