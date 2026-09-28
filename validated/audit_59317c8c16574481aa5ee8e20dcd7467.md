### Title
SignableTransaction fee/weight calculation omits the OP_RETURN output, so the transaction pays less than the requested fee rate - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` appends an OP_RETURN output to `tx_outs` when `data` is specified, but both calls to `calculate_weight_vbytes` pass only `payments` — never the OP_RETURN output. The resulting `weight`/`vbytes` (and therefore `needed_fee`) are computed for a smaller transaction than the one actually constructed and signed, mirroring the report's pattern of one accounting path including a component the other omits.

### Finding Description
At `send.rs:193-202`, the OP_RETURN output is pushed into `tx_outs`. The weight estimation at `send.rs:204` calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)`, where `tx.output` is built solely from `payments` (`send.rs:85-93`). The OP_RETURN output is never included in the estimated transaction. The same omission occurs in the change path at `send.rs:225-226`, which again passes `payments` without the data output. Consequently `needed_fee = fee_per_vbyte * vbytes` (`send.rs:206`, `send.rs:227`) is priced on a transaction missing up to ~90+ bytes (OP_RETURN script plus up to 80 bytes of data, plus output overhead). The final check `weight > MAX_STANDARD_TX_WEIGHT` (`send.rs:241`) also uses the underestimated weight, so a transaction that actually exceeds the standardness limit can pass the check.

### Impact Explanation
An unprivileged caller providing `data` causes the constructed `SignableTransaction` to (a) pay an effective fee rate strictly lower than `fee_per_vbyte` — `fee()` (`send.rs:138-141`) will return `needed_fee` as accounted, but the real feerate is `needed_fee / actual_vbytes < fee_per_vbyte` — and (b) potentially exceed `MAX_STANDARD_TX_WEIGHT` while passing the size check. Transactions may be non-standard/unrelayable or underpriced and fail to confirm, and any accounting relying on `needed_fee()`/`fee()` vs. the intended rate is inconsistent.

### Likelihood Explanation
`data` is an ordinary public input to `SignableTransaction::new`; any attempt to attach metadata triggers the undercount on every such transaction. No malicious validator or internal access is required.

### Recommendation
Include the OP_RETURN output in the weight estimation — e.g., build the full output list (payments + optional OP_RETURN) once and pass it to `calculate_weight_vbytes`, or refactor the estimator to take `&[TxOut]` built from `tx_outs`. Re-derive `weight`/`vbytes`/`needed_fee` after the outputs are finalized, and run the `MAX_STANDARD_TX_WEIGHT` check on that final value.

### Proof of Concept
`calculate_weight_vbytes(1, payments, None)` with `payments = []` produces a tx with 0 outputs. If `data = vec![0; 80]` is supplied, `tx_outs` gains a ~92-byte output that the estimator never sees. `vbytes` is identical whether or not `data` is set, so `needed_fee` is unchanged while the real transaction is ~23+ vbytes larger; `fee_per_vbyte * vbytes` < `fee_per_vbyte * actual_vbytes`, and `weight` can exceed `MAX_STANDARD_TX_WEIGHT` without triggering `TooLargeTransaction`.