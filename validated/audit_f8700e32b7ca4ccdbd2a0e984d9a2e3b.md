### Title
`SignableTransaction::new` excludes the OP_RETURN data output from weight/fee estimation, producing under-priced transactions that can be unbroadcastable - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` builds `tx_outs` including an OP_RETURN output for attacker-influenced `data`, but passes only `payments` (and optionally `change`) to `calculate_weight_vbytes`. The resulting `needed_fee` and the `TooLowFee` relay check are computed against a transaction that is missing the data output, so the actual signed transaction weighs up to ~90+ vbytes more than the fee was sized for. The declared fee rate is silently ignored — the analog of the reported "parameter ignored / wrong source of funds accounting" class: the real transaction's resources (weight) are not what the fee was debited against.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, `SignableTransaction::new` appends the OP_RETURN output to `tx_outs` at lines 194-202, yet both fee estimations call `Self::calculate_weight_vbytes(tx_ins.len(), payments, ...)` at lines 204 and 226 with `payments`, which omits the OP_RETURN output entirely. The produced `Transaction` (lines 245-251) does include the data output, so its real weight exceeds the estimate by `8 (amount) + ~1-3 (script len) + 1 (OP_RETURN) + pushdata prefix + len(data)` bytes of non-witness data — up to roughly 90 vbytes for the maximum allowed 80-byte `data` (checked at line 171).

Consequences:
- `needed_fee = fee_per_vbyte * vbytes` (line 206) and `fee_with_change` (line 227) are both understated. When a change output is created, the actual fee equals `fee_with_change` exactly (change = `input_sat - payment_sat - fee_with_change`, line 228-230), so the realized fee rate is strictly lower than `fee_per_vbyte`.
- The minimum-relay-fee guard at line 211 compares against the underestimated `vbytes`, so a transaction requested at the minimum relay rate can fall below `DEFAULT_MIN_RELAY_TX_FEE` for its true size once signed.
- `data` arrives via OutInstruction payloads, i.e., bytes an unprivileged user causes the threshold network to sign into a transaction.

### Impact Explanation
The threshold group produces a fully signed Bitcoin transaction whose effective fee rate is below the requested rate and potentially below the network minimum relay fee. Such a transaction will be rejected by Bitcoin Core mempool relay, rendering the signed spend unbroadcastable — the Serai-owned inputs are committed to a transaction that cannot confirm, forcing reconstruction/re-signing and stalling fund movement. This is a funds-availability failure caused purely by public input (the instruction `data` field), qualifying as Medium severity.

### Likelihood Explanation
Any OutInstruction carrying `data` triggers the underestimation; the discrepancy is largest with maximal `data` and few inputs/payments (where 90 vbytes is a large relative error). It materializes whenever the selected fee rate is near the relay minimum or when the operator expects `needed_fee()` to reflect the true signed size — `needed_fee()` (lines 133-135) reports the underestimated value while `fee()` (lines 138-141) reveals the shortfall only after the fact.

### Recommendation
Include the data output in the weight estimate: pass the actual `tx_outs` (payments + OP_RETURN, and change where applicable) into `calculate_weight_vbytes`, or add a `data_len` parameter so the OP_RETURN output's size is counted. Re-derive both `needed_fee` and `fee_with_change` from the full output set, and enforce the `TooLowFee` check against the final transaction's true vsize.

### Proof of Concept
Construct a `SignableTransaction` with one input, one payment, a change script, and `data = vec![0u8; 80]` at `fee_per_vbyte` equal to the minimum relay rate. The real transaction contains a 3rd output (~92 bytes, ~368 WU ≈ 92 vbytes) not present during `calculate_weight_vbytes`. The signed `Transaction`'s actual vsize exceeds the estimated `vbytes`, its paid fee equals `fee_with_change` computed for the smaller estimate, and the realized sat/vbyte rate drops below the relay minimum — Bitcoin Core rejects it with `min relay fee not met`, leaving the multisig's inputs unbroadcastable despite a valid threshold signature.

Relevant code: `networks/bitcoin/src/wallet/send.rs` lines 171-202 (data output appended after dust check), 204-235 (fee estimated without the data output), 245-255 (final tx includes it).