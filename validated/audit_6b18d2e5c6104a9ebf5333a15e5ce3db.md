### Title
`SignableTransaction::new` omits the OP_RETURN output from weight/fee calculation, producing underpriced or oversized transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the `sweep()` report — funds locked by a missing accounting/conversion step — `SignableTransaction::new` pushes the `OP_RETURN` data output into `tx_outs` but computes transaction weight, vbytes, `needed_fee`, the minimum-relay check, and the `MAX_STANDARD_TX_WEIGHT` check using only `payments`, never including the data output.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, the OP_RETURN output is appended to `tx_outs` (lines 193–202), but the weight/vbytes used for the fee are computed as `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` (line 204), where `payments` is the payment list only — the data output is never represented. The same omission occurs for the change case (lines 225–227). Consequences:

- `needed_fee = fee_per_vbyte * vbytes` is computed on a smaller vsize than the real transaction, so the change output value (line 228–230, `input_sat - payment_sat - fee_with_change`) leaves an actual fee that yields a lower sat/vbyte rate than requested.
- The `TooLowFee` check (line 211) validates `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the underestimated `vbytes`, so a transaction can pass this check while its real fee rate falls below the minimum relay fee — the transaction will not relay/confirm, and `fee()`/`needed_fee()` report values inconsistent with the signed result.
- The `weight > MAX_STANDARD_TX_WEIGHT` check (line 241) uses `weight` that excludes the OP_RETURN output's weight (~`8 + script_len` bytes serialized, times 4 weight units since it's non-witness data, plus output overhead), allowing a transaction over the standardness limit to be constructed as valid.

### Impact Explanation
A transaction built with `data` set is signed and broadcast with a fee lower than the caller's `fee_per_vbyte` intent, and potentially below the Bitcoin minimum relay fee or above `MAX_STANDARD_TX_WEIGHT`. Such a transaction is rejected by nodes or stalls unconfirmed, locking the selected UTXOs in a transaction that cannot confirm — the "funds stuck" shape of the reference bug (value committed to the contract/transaction but no path to successfully move it). Unlike the reference bug the funds are recoverable by re-signing without data, but the produced artifact is objectively invalid relative to the parameters requested, and `needed_fee()` misreports the fee accounting.

### Likelihood Explanation
`data` is a public parameter of `SignableTransaction::new` (line 154); any caller feeding untrusted output data hits this path deterministically. The miscalculation is unconditional whenever `data.is_some()` — no race or adversary needed. The degree of underpricing scales with the OP_RETURN payload size (up to the `TooMuchData`/`PushBytesBuf` limit), so low `fee_per_vbyte` combined with large data maximally risks falling under the relay minimum.

### Recommendation
Compute weight/vbytes over the full output set actually committed, e.g. pass an iterator of `(script_pubkey, value)` covering `payments` plus the OP_RETURN output (and the change output when applicable) into `calculate_weight_vbytes`, or build the candidate `tx_outs` first and weight that. Apply the `TooLowFee`, `NotEnoughFunds`, and `MAX_STANDARD_TX_WEIGHT` checks against that complete weight.

### Proof of Concept
In `networks/bitcoin/src/wallet/send.rs`:

- Line 194–202: `tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) })` — the data output exists in the final transaction.
- Line 204: `let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);` — computed over `payments` only.
- Line 206: `let mut needed_fee = fee_per_vbyte * vbytes;` — fee target ignores the data output's vbytes.
- Line 228–233: change value is `input_sat - (payment_sat + fee_with_change)`, where `fee_with_change` likewise omits the OP_RETURN weight, so the realized fee equals `fee_with_change` while the actual transaction is larger.
- Line 241: `weight > MAX_STANDARD_TX_WEIGHT` compares the undercounted `weight`.

Concretely: call `SignableTransaction::new(inputs, payments, Some(change), Some(vec![0u8; 80]), fee_per_vbyte)`; the signed transaction's real vsize exceeds `vbytes` by the serialized size of the OP_RETURN output, so `fee() / tx.vsize() < fee_per_vbyte`, and with `fee_per_vbyte` near the minimum relay rate the transaction fails relay despite passing the `TooLowFee` check.