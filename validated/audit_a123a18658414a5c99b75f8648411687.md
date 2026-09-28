### Title
OP_RETURN data output is excluded from transaction weight/vbytes, so the signed fee is computed on the wrong transaction size - (networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` accepts an arbitrary up-to-80-byte `data` payload and appends a zero-value `OP_RETURN` output to the transaction, but calculates the required fee via `calculate_weight_vbytes(tx_ins.len(), payments, ...)` which only expands `payments` (and optionally `change`) into the mock transaction. The `data` output's weight is never included, so `needed_fee` — and the `TooLowFee` min-relay check — are computed against a smaller vbytes figure than the transaction actually signed and broadcast.

### Finding Description
The analog to the oracle finding is a value returned in the wrong "units" because one leg of a multi-step conversion is silently dropped. In `send.rs`, `new` builds `tx_outs` with the OP_RETURN output pushed before the weight is measured (networks/bitcoin/src/wallet/send.rs:194-202), but the fee size is computed from a freshly constructed `Transaction` inside `calculate_weight_vbytes` that contains only `input` + `payments` (+ `change`) — no OP_RETURN (send.rs:68-99). At send.rs:204 and send.rs:225-226, the calls pass only `payments`, so:

- `vbytes` is short by roughly `8 (value) + 1 (script len) + 1 (OP_RETURN) + 1..2 (push opcode) + data.len()` weight-derived vbytes — up to ~90+ vbytes for an 80-byte payload.
- `needed_fee = fee_per_vbyte * vbytes` (send.rs:206) underreports the fee needed to hit the caller-specified rate.
- The minimum-relay guard `needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` (send.rs:211) is also evaluated against the understated vbytes, so a `fee_per_vbyte` intended to be just above the relay minimum can produce a transaction whose real feerate falls below `DEFAULT_MIN_RELAY_TX_FEE`.

When a change output exists, the change leg repeats the same omission: `calculate_weight_vbytes(..., payments, Some(&change))` (send.rs:225-226) still omits `data`, so `fee_with_change` is low and the change amount `input_sat - payment_sat - fee_with_change` (send.rs:228) is over-credited to change, compounding the feerate shortfall. The test in `networks/bitcoin/tests/wallet.rs:269` only asserts `needed_fee == vsize * FEE` for a data-less transaction, so the discrepancy is untested.

### Impact Explanation
An unprivileged caller supplying `data` (the documented `RefundableInInstruction`/InInstruction path carries user bytes into these transactions) causes the coordinator/processor to produce and sign a transaction whose effective feerate is lower than the requested `fee_per_vbyte`. In the worst case — `fee_per_vbyte` set near the relay minimum, or inputs that just cover `payment_sat + needed_fee` with no change output — the actual fee `sum(inputs) - sum(outputs)` can land below Bitcoin Core's minimum relay fee, making the signed transaction non-relayable. Funds are not burned, but the batch/payment is stuck until re-signed at a corrected fee, and `needed_fee()`/`fee()` report misleading values to accounting logic that relies on them.

### Likelihood Explanation
Any use of `SignableTransaction::new` with `data: Some(..)` triggers the miscalculation; the fee error grows linearly with `data.len()` and `fee_per_vbyte`. Whether it produces an unbroadcastable transaction depends on how close the chosen feerate and input surplus are to the relay minimum, but the fee is deterministically wrong whenever data is present — a concrete incorrect formula, not a timing edge.

### Recommendation
Include the OP_RETURN output in the mock transaction used by `calculate_weight_vbytes`: either push the actual `TxOut { value: ZERO, script_pubkey: op_return_script }` before computing `tx.weight()`, or add an explicit `data_len: Option<usize>` parameter that constructs the same OP_RETURN `ScriptBuf` inside the function. Apply the same correction to both the no-change call (send.rs:204) and the change call (send.rs:226), and add a test asserting `needed_fee == actual_vsize * fee_per_vbyte` for a transaction carrying a non-empty `data` payload.

### Proof of Concept
In `networks/bitcoin/tests/wallet.rs`, extend `test_data` (or add a case) that:

1. Creates `SignableTransaction::new(vec![output], &[], Some(change), Some(vec![0; 80]), FEE)`.
2. Signs it via `sign(&keys, &tx)` and computes `tx.vsize()`.
3. Asserts `tx.needed_fee() == vsize * FEE` — this fails, because `needed_fee` was computed without the ~91-vbyte OP_RETURN output, so `tx.needed_fee() < vsize * FEE` and `tx.fee() > needed_fee` while `tx.fee() / vsize < FEE`.
4. With `fee_per_vbyte` chosen so that `needed_fee` exactly equals the relay floor on the understated vbytes, the resulting signed transaction's real feerate falls below `DEFAULT_MIN_RELAY_TX_FEE` and would be rejected by `send_raw_transaction`.