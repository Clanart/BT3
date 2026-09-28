### Title
Fee formula omits the OP_RETURN data output, undercharging the transaction so it can fall below the minimum relay fee and never confirm - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the reported bug — a distribution denominator (`YT` total supply) that fails to account for balance actually withdrawn — `SignableTransaction::new` computes the transaction's required fee from a weight formula whose denominator (counted outputs) fails to account for the OP_RETURN output that is actually added to the transaction. An attacker-supplied `data` payload inflates the real transaction size while the fee is computed as if the output did not exist, so the effective fee rate is lower than the caller-specified `fee_per_vbyte` and can silently fall below the minimum relay fee.

### Finding Description
`SignableTransaction::new` appends an `OP_RETURN` output to `tx_outs` when `data` is specified (`send.rs:194-202`), but the weight/vbyte calculation performed at `send.rs:204` calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)`, which builds the mock transaction only from `payments` — it never includes the OP_RETURN output (`send.rs:85-93`). The change-output recalculation at `send.rs:225-226` similarly uses `payments`, again excluding the data output.

`needed_fee = fee_per_vbyte * vbytes` (`send.rs:206`) is therefore computed against a vbytes figure that omits up to ~83 weight-bearing bytes (the 80-byte payload plus output overhead). The minimum-relay-fee check at `send.rs:211` also uses this understated `vbytes`, so a transaction can pass the `TooLowFee` check while its *actual* fee rate (`fee / actual_vsize`) is below `DEFAULT_MIN_RELAY_TX_FEE`.

The bug class maps directly: just as the Napier formula distributes rewards over `YT` supply without subtracting entitlement already extracted as claimed YBT (giving claimers disproportionate reward), the fee formula distributes the fee burden over a vsize that excludes bytes actually added to the transaction (giving the data-payload path an unpriced discount that pushes the real fee rate below policy minimums).

### Impact Explanation
A `SignableTransaction` created with a `data` payload pays fewer satoshis per actual vbyte than requested. At boundary fee rates, the signed transaction is nonstandard/underpaying, is rejected or never relayed/mined, and the consumed `ReceivedOutput` inputs cannot be confirmed spent — funds become unspendable in practice until a replacement transaction is constructed. The `fee()`/`needed_fee()` accessors report a fee that does not correspond to the transaction's real fee rate, making the accounting formula incorrect.

### Likelihood Explanation
Reachable by any caller passing a non-`None` `data` argument (up to 80 bytes is explicitly permitted, per `send.rs:186-191` test expectations) together with a low `fee_per_vbyte`. The discrepancy is deterministic: every data-carrying transaction is undercharged by roughly the OP_RETURN output's weight, and near the minimum relay fee the transaction is guaranteed to underpay.

### Recommendation
Include the OP_RETURN output in the transaction built inside `calculate_weight_vbytes` (or add a `data: Option<&[u8]>` parameter that pushes the `ScriptBuf::new_op_return` output before computing `tx.weight()`), so `needed_fee`, the `TooLowFee` check, and the change-output recomputation are all evaluated against the true final vsize.

### Proof of Concept
Textual: construct `SignableTransaction::new(inputs, &payments, None, Some(vec![0; 80]), fee_per_vbyte)` where `fee_per_vbyte` is exactly the minimum relay rate (1 sat/vbyte effective). `needed_fee` is computed from a mock transaction containing only the payment outputs (`send.rs:204`), while the returned `self.tx` additionally contains the ~89-weight-unit OP_RETURN output (`send.rs:194-202`). The resulting `tx.vsize()` exceeds the `vbytes` used in the fee check at `send.rs:211`, so `fee() / tx.vsize() < DEFAULT_MIN_RELAY_TX_FEE`; `fee()` - `needed_fee()` still equals the leftover-inputs difference, confirming the fee was priced on the wrong (smaller) denominator. The test at `send.rs:186-187` asserts such a construction is accepted as `Ok`, demonstrating the underpriced transaction is produced rather than rejected.