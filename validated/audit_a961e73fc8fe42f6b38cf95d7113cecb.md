The bug class is incorrect payment accounting — the caller believes the fee/amount charged covers the operation, while the code actually settles a different amount. In `networks/bitcoin/src/wallet/send.rs`, `SignableTransaction::new` appends the caller-supplied `data` OP_RETURN output to `tx_outs`, but then computes `weight`/`vbytes` and `needed_fee` from `calculate_weight_vbytes(tx_ins.len(), payments, None)`, which rebuilds a transaction containing *only* `payments` (and optionally `change`) — the OP_RETURN output is never included in the fee calculation.

### Title
OP_RETURN data output excluded from weight/fee calculation, underpaying the miner fee below the intended rate — ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
`SignableTransaction::new` accepts an optional `data` parameter that is pushed onto the real transaction as an OP_RETURN `TxOut`, but the subsequent `calculate_weight_vbytes` call reconstructs a transaction from `payments` only (it never sees `tx_outs` or `data`). The reported/used `needed_fee` therefore covers fewer vbytes than the transaction actually occupies.

### Finding Description
- The OP_RETURN output is appended to the real outputs at `send.rs:194-202` (`tx_outs.push(... ScriptBuf::new_op_return ...)`).
- The weight/vbytes used for the fee is computed at `send.rs:204` via `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)`, and inside that function (`send.rs:85-99`) only `payments` and an optional `change` script are turned into outputs — the data output is absent.
- The minimum-fee sanity check at `send.rs:211` (`needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`) uses the *same* underestimated `vbytes`, so it cannot catch the shortfall.
- When a change output exists, `fee_with_change` at `send.rs:225-234` is likewise computed without the OP_RETURN output, so the change is over-credited and the effective fee underpaid; `needed_fee` reported to callers (`send.rs:129-135`, consumed by `processor/src/networks/bitcoin.rs`) does not reflect the transaction's true feerate.
- `data` is an untrusted public input (up to 80 bytes, checked at `send.rs:171-173`), so the caller directly controls the size discrepancy.

### Impact Explanation
The resulting transaction pays `fee_per_vbyte * vbytes` for a transaction that is larger than `vbytes` — the effective sat/vbyte is lower than requested, and can fall below `DEFAULT_MIN_RELAY_TX_FEE` on the real transaction while passing the check on the phantom one. Combined with `Sequence::MAX` (non-RBF, `send.rs:78`), the transaction cannot be fee-bumped and the inputs may be unrelayable/stuck — funds committed to inputs that cannot confirm, i.e. value locked despite the wallet reporting a correctly-priced fee.

### Likelihood Explanation
Any invocation of `SignableTransaction::new`/`TransactionMachine` path with `data: Some(..)` triggers it; the error grows linearly with `data` length (~90+ vbytes discrepancy at the 80-byte max). It does not require an attacker — it is deterministic whenever data is attached — though a malicious party supplying large `data` maximizes the shortfall.

### Recommendation
Include the OP_RETURN output in the transaction built by `calculate_weight_vbytes` — e.g. pass the already-constructed `tx_outs` (or a `data: Option<&PushBytesBuf>`/`ScriptBuf` for the OP_RETURN script) into `calculate_weight_vbytes` both at `send.rs:204` and in the change branch at `send.rs:225-226`, so `vbytes`/`needed_fee` reflect the real transaction. Alternatively, compute weight directly on the final `tx_outs` rather than rebuilding from `payments`.

### Proof of Concept
Construct `SignableTransaction::new(inputs, &payments, None, Some(vec![0u8; 80]), fee_per_vbyte)`. The returned `needed_fee()` equals `fee_per_vbyte * vsize(tx_without_op_return)`, while `transaction()` contains an extra ~90-vbyte OP_RETURN output. `fee() / actual_vsize < fee_per_vbyte`, and with a low `fee_per_vbyte` the real transaction can sit below `DEFAULT_MIN_RELAY_TX_FEE` while the check at `send.rs:211` passes — confirmed by `tx.vsize()` exceeding the `vbytes` value used in the fee computation.