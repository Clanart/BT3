### Title
`SignableTransaction::new` omits the OP_RETURN output from weight/vbytes when computing the fee, underpaying the fee for transactions carrying `data` - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` accepts an optional `data` payload which is appended as an OP_RETURN `TxOut` to `tx_outs`. However, the weight and vbytes used to compute `needed_fee` are derived from `calculate_weight_vbytes(tx_ins.len(), payments, ...)`, which only expands `payments` (and optionally `change`) — never the OP_RETURN output. The fee therefore does not account for the data output's weight, analogously to fees being charged but not correctly accounted for.

### Finding Description
At `send.rs:194-202` the OP_RETURN output is pushed onto `tx_outs`. The fee calculation at `send.rs:204` calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)`, and the change-aware recalculation at `send.rs:225-226` calls it with `payments` and `Some(&change)`. In both cases the transaction skeleton inside `calculate_weight_vbytes` (`send.rs:68-99`) is built only from `payments` and `change`; the OP_RETURN `TxOut` (8-byte value + compactsize length + `OP_RETURN` opcode + pushdata header + up to 80 bytes of data, ~90 bytes ≈ ~90 vbytes) is excluded from `weight`/`vbytes`.

Consequences:
1. `needed_fee = fee_per_vbyte * vbytes` undercharges by `fee_per_vbyte * (OP_RETURN output vsize)`. Since `fee()` returns `sum(inputs) - sum(outputs)`, the transaction actually pays only `needed_fee` (when there is no change, or change absorbs the difference), so the realized fee rate is strictly below the caller-requested `fee_per_vbyte`.
2. The `TooLowFee` check at `send.rs:211` validates `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the same underestimated `vbytes`. A caller can supply `data` such that the transaction passes the check yet its true fee rate (`fee / actual_vsize`) is below the minimum relay fee, making it non-standard/unrelayable.
3. The `TooLargeTransaction` weight check at `send.rs:241` uses `weight` that excludes the OP_RETURN output, so a transaction at the boundary can exceed `MAX_STANDARD_TX_WEIGHT` once broadcast.

### Impact Explanation
An unprivileged caller controlling `data` (and `payments`/`change`) can produce a `SignableTransaction` that the threshold network signs and broadcasts with a fee rate below what was requested — and potentially below the Bitcoin default minimum relay fee or above the max standard weight — causing the transaction to be rejected by relay or stuck unconfirmed. Funds are not lost, but the intended fee accounting is violated and spends can fail to propagate, analogous to accrued fees not being properly accounted for.

### Likelihood Explanation
Reachable by any caller passing a non-empty `data` argument (up to 80 bytes) to `SignableTransaction::new`; no privileged position needed. The magnitude of underpayment scales with `fee_per_vbyte` and data size. Whether the TX falls below relay minimums depends on chosen fee rate and data size, so impact ranges from a silently lower fee rate to a non-relayable transaction.

### Recommendation
Include the OP_RETURN output in the skeleton used by `calculate_weight_vbytes` — e.g., append a `TxOut { value: Amount::ZERO, script_pubkey: <op_return script> }` of equal serialized size to the `output` vector (or pass the constructed `tx_outs` rather than `payments`), so `needed_fee`, the `TooLowFee` check, and the `TooLargeTransaction` check all reflect the true transaction size.

### Proof of Concept
```rust
// Conceptual: SignableTransaction::new(inputs, payments, change, Some(data), fee_per_vbyte)
// with data = vec![0u8; 80].
// tx_outs contains payments + OP_RETURN(80 bytes) [+ change].
// calculate_weight_vbytes is invoked with `payments` only, so its `tx.output`
// lacks the ~90-vbyte OP_RETURN output.
// needed_fee = fee_per_vbyte * vbytes   // vbytes too small
// Actual TX size when signed/broadcast = vbytes + ~90 vbytes.
// Effective fee rate = needed_fee / (vbytes + ~90) < fee_per_vbyte.
// If fee_per_vbyte was chosen so needed_fee ~= min_relay * vbytes / 1000,
// the broadcast TX is below the default minimum relay fee and will not propagate.
```
Relevant code: `send.rs:194-204` (OP_RETURN appended, then weight computed without it), `send.rs:211` (`TooLowFee` on wrong vbytes), `send.rs:228-233` (change absorbs only the under-estimated fee), `send.rs:241` (weight check on undercounted weight).