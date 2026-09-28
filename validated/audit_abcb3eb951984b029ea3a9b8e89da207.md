### Title
`SignableTransaction::new` computes `needed_fee` over a transaction that omits the OP_RETURN `data` output, producing a signed transaction whose real feerate is below the requested rate (and potentially below mempool minimum) - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` accepts an attacker-influenced `data` field which is pushed onto `tx_outs` as an OP_RETURN output before the weight/fee calculation. However, `calculate_weight_vbytes` is invoked with only `payments` (and optionally `change`), never with the data output. The resulting `needed_fee` is priced for a smaller transaction than the one actually signed, so the effective sat/vbyte of the signed transaction is lower than `fee_per_vbyte`.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is appended to `tx_outs` at lines 194–202 of `networks/bitcoin/src/wallet/send.rs`, but the vbytes used for `needed_fee = fee_per_vbyte * vbytes` come from `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at line 204, which builds a weight-estimation transaction containing only the payment outputs. The change path at lines 224–235 repeats the same omission: `fee_with_change = fee_per_vbyte * vbytes_with_change` also excludes the OP_RETURN output, while the change amount is derived as `input_sat - payment_sat - fee_with_change`.

The minimum-relay-fee sanity check at line 211 (`needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`) uses the same underestimated `vbytes`, so it cannot catch the shortfall. When `multisig`/`sign` is later invoked, the sighash commits via `Prevouts::All` to the full transaction — including the OP_RETURN — so a valid FROST signature is produced for a transaction whose actual feerate is `needed_fee / actual_vbytes < fee_per_vbyte`.

This is the Serai analog of the Value DeFi bug class: the protocol computes a critical economic value (withdrawal price there, transaction weight/fee here) using a function that does not account for all attacker-controlled inputs, producing a mispriced result that is then finalized.

### Impact Explanation
An attacker who can influence a payment's `data` field (up to 80 bytes, public input) can cause the threshold signing group to produce and broadcast a Bitcoin transaction whose real feerate is up to ~90 vbytes' worth of fee lower than intended. If `fee_per_vbyte` was set at or near the mempool minimum relay feerate, the resulting signed transaction is below the minimum and will not propagate or confirm — locking the spent `ReceivedOutput`s until a re-sign with a new transaction occurs. With change, the change amount is also inflated by the unaccounted fee, meaning less fee is paid than even the underestimated `needed_fee` would imply is correct. This is an incorrect fee-formula bug reachable from public transaction-construction inputs.

### Likelihood Explanation
Any caller supplying a `data` payload triggers the underpayment; the larger the data, the larger the feerate error. Exploitation requires no key material or collusion — only the ability to have a payment with an OP_RETURN payload constructed (a normal, unprivileged input to the wallet API). The error is deterministic, not probabilistic.

### Recommendation
Include the OP_RETURN output in the weight-estimation transaction inside `calculate_weight_vbytes` (pass the fully built `tx_outs`, or add a `data: Option<&[u8]>` parameter that appends the same `ScriptBuf::new_op_return` output before computing `weight`). Ensure both the no-change and with-change fee paths price the exact output set that will be signed, and re-check the minimum-relay bound against the final vbytes.

### Proof of Concept
In `SignableTransaction::new` (networks/bitcoin/src/wallet/send.rs:150-255):
1. `tx_outs` gains `TxOut { value: ZERO, script_pubkey: new_op_return(data) }` at lines 194-202.
2. `calculate_weight_vbytes(tx_ins.len(), payments, None)` at line 204 and `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` at line 226 both construct an estimation `Transaction` whose `output` list contains only `payments` plus optional change — the OP_RETURN is never represented (lines 85-99).
3. Therefore `vbytes`/`vbytes_with_change` are ~`(8 + 1 + data.len() + push overhead)` bytes short; `needed_fee` and `fee_with_change` are underpriced by `fee_per_vbyte * missing_vbytes`.
4. `needed_fee()` returns this low fee, `fee()` confirms `actual fee == needed_fee`, yet `tx.weight()` of the final transaction is higher than `weight`, so `actual_fee / actual_vbytes < fee_per_vbyte`. A caller requesting `fee_per_vbyte` equal to the mempool minimum produces a validly-signed but non-relayable transaction.