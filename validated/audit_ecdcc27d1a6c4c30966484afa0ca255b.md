### Title
`SignableTransaction::new` omits the OP_RETURN output from weight/fee calculation, producing an under-priced or oversized transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
When a `SignableTransaction` is constructed with `data` (an OP_RETURN output of up to 80 bytes), the output is appended to `tx_outs`, but `calculate_weight_vbytes` is called with `payments` only. The weight, vbytes, `needed_fee`, minimum-relay-fee check, and `MAX_STANDARD_TX_WEIGHT` check are all computed on a transaction that lacks the OP_RETURN output. The result is a signed transaction that pays a lower fee rate than requested and can exceed consensus/policy limits — the value difference between `needed_fee` and the actual fee charged (`input_sat - output_sat`) is silently absorbed as extra fee or produces a transaction the network rejects.

### Finding Description
`SignableTransaction::new` builds `tx_outs` including the OP_RETURN output at send.rs:194-202, but then calculates weight via `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at send.rs:204, which constructs a dummy transaction containing only `payments` (send.rs:85-93) and never the data output. The same omission occurs in the change branch at send.rs:225-226. Consequently:

1. `needed_fee = fee_per_vbyte * vbytes` (send.rs:206) undercounts by the size of the OP_RETURN output (~90+ vbytes for a full 80-byte payload).
2. The `TooLowFee` guard at send.rs:211 compares against the underestimated `vbytes`, so a transaction can pass the check while its true fee rate is below `DEFAULT_MIN_RELAY_TX_FEE`, causing relay rejection.
3. The `MAX_STANDARD_TX_WEIGHT` check at send.rs:241 uses the underestimated `weight`, so a transaction can be signed that exceeds the standardness weight limit and is rejected by nodes.
4. When change is added, `fee_with_change` (send.rs:227) is also computed without the OP_RETURN, so the change amount can be over-credited, further depressing the real fee rate — or a change output can be created when the real leftover (after paying for the OP_RETURN weight) should have been dust burned as fee.

This is the Serai analog of the IchiVaultSpell issue: value computed on an incomplete accounting of the transaction — the "leftover" is not correctly attributed. The multisig then signs via `Prevouts::All` (send.rs:375) a transaction whose fee/recipient allocation was derived from the wrong weight.

### Impact Explanation
An attacker or integrator supplying `data` causes the threshold group to sign a Bitcoin transaction with a systematically under-charged fee. At minimum the transaction confirms at a lower effective feerate than `fee_per_vbyte` requested (silent over/under-accounting of funds). With a large payload (near 80 bytes) and a marginal `fee_per_vbyte`, the transaction falls below `DEFAULT_MIN_RELAY_TX_FEE` or `MAX_STANDARD_TX_WEIGHT` and is rejected by the network — inputs remain locked as "spent pending" while funds are never delivered, i.e., funds reported sent that are not spendable. The excess `input_sat - sum(outputs)` is burned as miner fee without the callers' intent.

### Likelihood Explanation
Reachable by any party able to submit a `data` payload for an outbound transaction (`SignableTransaction::new` is a public API and `data` is untrusted bytes feeding the sighash the validators sign). The miscalculation is deterministic whenever `data.is_some()`; severity scales with payload length and requested feerate.

### Recommendation
Include the OP_RETURN output in the weight calculation. Change `calculate_weight_vbytes` to accept the final `tx_outs` (or a `data` parameter) and push `TxOut { value: Amount::ZERO, script_pubkey: op_return }` into the dummy transaction before measuring weight — both for the no-change call at send.rs:204 and the change call at send.rs:226. Alternatively compute weight from the actually-constructed `Transaction`.

### Proof of Concept
1. Call `SignableTransaction::new(inputs, payments, change, Some(vec![0xAA; 80]), fee_per_vbyte)` where `fee_per_vbyte` equals the minimum relay rate (`DEFAULT_MIN_RELAY_TX_FEE / 1000` per vbyte).
2. The `TooLowFee` check at send.rs:211 passes because `vbytes` excludes the ~91-vbyte OP_RETURN output.
3. After signing via `TransactionMachine` (send.rs:373-397), the real transaction's vsize exceeds `vbytes` by the OP_RETURN size, so its actual fee rate `fee() / actual_vsize` is below the minimum relay fee — the transaction is rejected by `send_raw_transaction`, while `needed_fee()`/`fee()` still report the intended values. The inputs are committed to an un-broadcastable transaction.