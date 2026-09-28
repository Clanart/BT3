### Title
Fee/weight calculation omits the caller-supplied OP_RETURN output, causing underestimated fees and potentially non-standard transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` appends a data-carrying OP_RETURN output to `tx_outs`, but computes `vbytes`, `needed_fee`, and the `MAX_STANDARD_TX_WEIGHT` check using `calculate_weight_vbytes(tx_ins.len(), payments, ...)`, which only reconstructs the transaction from `payments` and `change`. The OP_RETURN output's weight is therefore never included in the solvency-style accounting (`input_sat >= payment_sat + needed_fee`) nor in the standardness bound — the same class of bug as a liquidity check that ignores funds which will be spent: a cost the transaction will actually incur is excluded from the check that is supposed to cover it.

### Finding Description
At `send.rs:193-202` the OP_RETURN output is pushed into `tx_outs` when `data` is supplied. The fee and weight estimation at `send.rs:204` and `send.rs:225-226` calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, ...)`, which builds a mock transaction whose `output` list is derived solely from `payments` (plus optional `change`) at `send.rs:85-99` — the OP_RETURN output is absent. Consequently:

1. `needed_fee = fee_per_vbyte * vbytes` (`send.rs:206`, `send.rs:227`) is computed for a smaller transaction than the one actually built and signed.
2. The adequacy check `input_sat < payment_sat + needed_fee` (`send.rs:215`) validates inputs against an understated fee.
3. The standardness check `weight > MAX_STANDARD_TX_WEIGHT` (`send.rs:241`) uses a weight that excludes up to ~83 bytes (~350+ weight units) of OP_RETURN output, so a transaction assembled at the boundary can exceed the standard weight limit and be rejected by relay policy.
4. `fee()` (`send.rs:138-141`) correctly reports `sum(inputs) - sum(outputs)`, but the invariant `fee() >= needed_fee` (the documented "fee necessary to achieve the fee rate specified at construction", `send.rs:129-132`) no longer holds when `data` is present — the transaction pays a lower effective sat/vbyte than requested.

`data` is an untrusted, caller-controlled parameter (up to 80 bytes per the `TooMuchData` check at `send.rs:171-173`), so any unprivileged caller of this public wallet API reaches the path.

### Impact Explanation
A signed transaction produced with `data` set pays strictly less than the requested fee rate — in the worst case (large OP_RETURN, fee rate at the minimum relay threshold) the actual rate can fall below `DEFAULT_MIN_RELAY_TX_FEE`, making the transaction non-relayable/stuck even though `TooLowFee` was checked. Transactions sized near `MAX_STANDARD_TX_WEIGHT` can silently exceed the standard limit and be rejected by the network. For a threshold wallet, this means the multisig signs a transaction whose committed fee/weight properties diverge from what the constructor verified — funds locked in the input may be unspendable via this transaction (underpaid fee / non-standard), degrading to a liveness/locked-funds failure rather than silent loss.

### Likelihood Explanation
Triggered whenever `SignableTransaction::new` is called with `Some(data)` — a fully public input path. No attacker collusion, timing, or secret access needed. The mis-sizing is deterministic for every non-empty `data`; whether it causes a dropped/stuck transaction depends on how close the requested fee rate and weight are to policy boundaries.

### Recommendation
Include the OP_RETURN output in the mock transaction used by `calculate_weight_vbytes` — e.g., pass the fully built `tx_outs` (payments + OP_RETURN + optional change) or add a `data: Option<&ScriptBuf>` parameter — and use that same output set for both the `needed_fee` computation and the `MAX_STANDARD_TX_WEIGHT` check, so the checked accounting matches the transaction actually signed.

### Proof of Concept
In `SignableTransaction::new`, call with one input, one payment, and `data = vec![0u8; 80]`:

- `tx_outs` gains an OP_RETURN output (`send.rs:194-202`), but `vbytes` at `send.rs:204` is measured on a transaction containing only the payment output.
- Resulting `needed_fee` is short by `fee_per_vbyte * (OP_RETURN output vbytes)` (~90+ WU ≈ 22+ vbytes for 80-byte data plus output overhead).
- `tx.fee()` (actual) `<` `fee_per_vbyte * actual_vsize`, i.e. the transaction underpays relative to the specified rate; if `fee_per_vbyte` equaled the min relay rate, the real transaction is below min relay fee despite passing the `TooLowFee` check at `send.rs:211-213`.