### Title
Fee calculation omits the OP_RETURN data output from transaction weight, undercharging the requested fee rate - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` computes `needed_fee` and the minimum-fee check from a weight estimate that includes only inputs and payments. The OP_RETURN output constructed from the caller-supplied `data` argument is pushed onto `tx_outs` but is never passed into `calculate_weight_vbytes`, so the funded fee rate is lower than `fee_per_vbyte`, and can fall below the relay minimum even though `TooLowFee` passed.

### Finding Description
The bug class from the report — a cost derived from only part of the serialized payload rather than the full transaction — maps directly onto `SignableTransaction::new` in bitcoin-serai.

At `send.rs:194-202`, when `data` is `Some`, an OP_RETURN `TxOut` is appended to `tx_outs`. At `send.rs:204`, the weight/vbyte estimate is computed as `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)`, i.e. only inputs + payments, never including the data output. `needed_fee = fee_per_vbyte * vbytes` (line 206) and the `DEFAULT_MIN_RELAY_TX_FEE` check (line 211) both use this underestimated `vbytes`, while the final transaction committed to signatures (lines 245-255) carries the extra output.

The gap is bounded (data ≤ 80 bytes, `TooMuchData` at line 171), so the OP_RETURN output costs roughly `(1 + 8 + 1 + 1 + 80) ≈ 91` bytes ≈ 364 weight units ≈ 91 vbytes, which is material relative to a minimal transaction (~150-250 vbytes). The change path has the same asymmetry in reverse: `fee_with_change` correctly includes the change output (lines 225-227), showing the author knew outputs affect weight but missed the data output.

### Impact Explanation
A transaction built with `data` pays an effective fee rate strictly below `fee_per_vbyte`, and can pass `TooLowFee` while its real sat/vbyte is under `DEFAULT_MIN_RELAY_TX_FEE`. For a threshold wallet whose transactions are signed by a FROST multisig (`TransactionSignMachine::sign`, lines 355-397, commits to the full `tx` via `Prevouts::All`), the result is a fully-signed transaction that nodes may refuse to relay or that confirms far slower than priced — the same "user pays less than intended / cost under-calculated" outcome as the Omni report, here burning signature shares on a tx that underpays miners.

### Likelihood Explanation
Reachable by any unprivileged caller of the public `SignableTransaction::new` API supplying `data`. No validator compromise or internal state is required; the miscalculation is deterministic. It is bounded by the 80-byte cap, so severity is Medium rather than High — the underpayment is at most ~91 vbytes worth of fee and cannot steal funds, only degrade/invalidate the produced transaction's feerate.

### Recommendation
Include the OP_RETURN output in the weight estimate, e.g. compute `calculate_weight_vbytes` over the actual `tx_outs` (payments + data output) before pushing change, or add the serialized size of the data output as a fixed overhead. Also enforce `TooLowFee` against the final vbytes including all outputs.

### Proof of Concept
Conceptually: call `SignableTransaction::new(inputs, &payments, None, Some(vec![0; 80]), fee_per_vbyte)`. `needed_fee` equals `fee_per_vbyte * vbytes` where `vbytes` excludes the ~91-byte OP_RETURN output, while `transaction().output` contains it. `fee() / actual_vsize < fee_per_vbyte`, and for `fee_per_vbyte` chosen near the relay floor the signed transaction's real feerate drops below `DEFAULT_MIN_RELAY_TX_FEE` despite the `TooLowFee` guard passing.