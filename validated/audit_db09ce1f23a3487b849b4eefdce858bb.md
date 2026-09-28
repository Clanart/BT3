### Title

OP_RETURN data output omitted from weight/fee calculation, allowing a signed transaction to exceed `MAX_STANDARD_TX_WEIGHT` and become unrelayable - (File: networks/bitcoin/src/wallet/send.rs)

### Summary

`SignableTransaction::new` computes the transaction weight and vbytes via `calculate_weight_vbytes`, which builds a template transaction from only `inputs`, `payments`, and an optional `change` output. The OP_RETURN output carrying `data` (up to 80 bytes) is appended to the real transaction but is never included in the template, so both the fee (`needed_fee`) and the standardness weight check at the end of `new` are computed on a transaction that is ~92 bytes / ~368 weight units smaller than the transaction actually produced and signed.

### Finding Description

In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150-256`):

1. The OP_RETURN output is pushed onto `tx_outs` at lines 194-202, before any weight calculation.
2. `calculate_weight_vbytes` is invoked at line 204 as `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — `payments` does not include the data output, and there is no parameter for it.
3. `needed_fee = fee_per_vbyte * vbytes` (line 206) and the `TooLowFee` check (lines 211-213) therefore price a smaller transaction than the one that will be signed.
4. The change-branch recalculation at lines 225-226 also omits the data output.
5. The final standardness guard `if weight > MAX_STANDARD_TX_WEIGHT` (lines 241-243) uses the underestimated `weight`.

The result is a signed `Transaction` whose actual weight exceeds what was checked. When the true weight crosses `MAX_STANDARD_TX_WEIGHT` (400,000 WU ≈ ~1,700 key-spend inputs), the fully-signed transaction is non-standard: Bitcoin Core rejects it from the mempool with `tx-size`/`bad-txns-*` errors even though it is consensus-valid. Because `TransactionMachine::sign` uses `TapSighashType::Default` with `Prevouts::All` (lines 373-390), the produced signatures commit to the exact oversized transaction — they cannot be replayed on a smaller, standard transaction. Additionally, even below the weight cap, the underpriced fee can fall below `DEFAULT_MIN_RELAY_TX_FEE` on the real vsize, again producing a transaction that cannot enter the mempool.

### Impact Explanation

This is the direct analog of the upstream bug: the sequencer/constructor path produces a transaction that cannot be "enqueued" (relayed into the mempool), even though the code path that should have enforced the bound exists. Funds controlled by the threshold key are locked into an unbroadcastable transaction: the signatures produced bind to the oversized transaction via SIGHASH_ALL semantics (`Prevouts::All`), so they authorize spending the inputs but only in a transaction the network will not relay. The inputs are effectively frozen until a new, standard-weight plan is constructed and re-signed, and the consumed preprocesses/shares are burned.

### Likelihood Explanation

Reaching the weight cap requires a large spend (on the order of ~1,700 P2TR key-spend inputs, or fewer with many large payment scripts), which the aggregation batcher can plausibly assemble when sweeping many received outputs — this is an operational batching concern, not an exotic input. The `data` field is supplied per-payment/instruction from externally-originated data and is bounded only at 80 bytes in this function, so the missing weight contribution is attacker- or user-influenceable. The fee-underpricing variant triggers on any transaction carrying `data`, since the real vsize always exceeds the priced vsize; whether it drops below the relay floor depends on the requested `fee_per_vbyte`. Severity is Medium: impact is a locked/unbroadcastable batch transaction rather than direct theft, and trigger conditions depend on batch size and fee parameters.

### Recommendation

Include the constructed OP_RETURN output (and any other dynamically appended outputs) in the transaction passed to `calculate_weight_vbytes`. The simplest fix is to compute the weight on the final `tx_outs` list — e.g., perform the dust/fee checks, build the complete output set (payments + OP_RETURN + change), and run `tx.weight()` on the assembled `Transaction` rather than reconstructing a template that can drift out of sync with what is actually signed.

### Proof of Concept

```rust
// In SignableTransaction::new (networks/bitcoin/src/wallet/send.rs)
// tx_outs gets the OP_RETURN output:
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(PushBytesBuf::try_from(data).unwrap()),
  })
}

// but the template built inside calculate_weight_vbytes only contains
// payments (+ optional change):
//   tx.output = payments.iter().map(|payment| TxOut { ... })   // no OP_RETURN
//
// so `weight` returned here is ~368 WU below the real transaction weight
// (8-byte value + ~3-byte script header + up to 82-byte OP_RETURN script).
let (mut weight, vbytes) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

// Later, this check passes with the underestimated weight:
if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
  Err(TransactionError::TooLargeTransaction)?;
}
// while self.tx (which includes the OP_RETURN output) has weight
// actual_weight = weight + ~368, which can exceed MAX_STANDARD_TX_WEIGHT,
// producing a signed transaction Bitcoin nodes will reject from the mempool.
```