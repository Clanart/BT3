### Title
`SignableTransaction::new` omits the OP_RETURN data output from the vbytes/weight calculation, producing a systematically underestimated fee and an incorrect max-weight check - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The external report's bug class is a quantity computed with inconsistent scaling/inputs (mixing 10^18 and 10^36 denominators so the computed amount differs from the real amount). The same shape exists in `SignableTransaction::new`: the fee and weight are derived from `calculate_weight_vbytes(tx_ins.len(), payments, None)`, which builds a template `Transaction` containing only the payment outputs — the OP_RETURN `data` output (pushed to `tx_outs` earlier) is never included. The result is that `needed_fee = fee_per_vbyte * vbytes` and the `MAX_STANDARD_TX_WEIGHT` check are computed against a transaction smaller than the one actually signed and broadcast.

### Finding Description
At `send.rs` the data output is appended to `tx_outs` before the size calculation:

```rust
// send.rs:194-202
if let Some(data) = data {
  tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) })
}

// send.rs:204
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

`calculate_weight_vbytes` (send.rs:62-127) constructs its template transaction's `output` solely from `payments` plus an optional `change` output — it has no parameter for the data output. Consequently:

- `needed_fee` (send.rs:206, 232) is `fee_per_vbyte * vbytes` where `vbytes` excludes the ~`data.len() + 10` byte OP_RETURN output.
- When `change` is provided, the change output value is `input_sat - payment_sat - fee_with_change` (send.rs:228-230), so the actual fee paid equals `fee_with_change`, while the real virtual size is larger. The effective fee rate is therefore strictly below `fee_per_vbyte`.
- The `TooLowFee` check (send.rs:211) uses the same underestimated `vbytes`, so a transaction that is actually below `DEFAULT_MIN_RELAY_TX_FEE` when broadcast can pass validation.
- The `TooLargeTransaction` check (send.rs:241) uses `weight` that never accounts for the data output (both the no-change and `weight_with_change` paths omit it).

Both `TransactionSignMachine::sign` (send.rs:383-390, sighash commits to `Prevouts::All` and the real `tx` including the OP_RETURN) and `TransactionSignatureMachine::complete` then finalize this underpriced transaction.

### Impact Explanation
`needed_fee()` is a public accessor documented as "the fee necessary for this transaction to achieve the fee rate specified at construction" — it returns a value that does not achieve that rate. Any caller supplying `data` produces a signed transaction paying less sat/vbyte than requested; in the worst case the effective fee rate falls under the default relay minimum despite passing the `TooLowFee` guard, yielding a signed transaction the network won't relay. This is a correctness mismatch between the computed quantity and the actual quantity, the same consequence class as the DODO finding (a returned amount that is larger/smaller than the true value due to inconsistent bases for the calculation).

### Likelihood Explanation
Deterministic whenever `data` is `Some` — the OP_RETURN output is unconditionally excluded from both `calculate_weight_vbytes` calls. The only attenuating factor is that `data` is capped at 80 bytes (send.rs:171-173), bounding the error to roughly 80-90 vbytes of missing weight; at high `fee_per_vbyte` this is a modest absolute error, but the underpayment relative to the caller's intent and the bypass of the minimum-fee guard are unconditional.

### Recommendation
Pass the data output (or the fully-built `tx_outs`) into `calculate_weight_vbytes` so `weight`/`vbytes` reflect the transaction actually being constructed. Reorder `SignableTransaction::new` to build the complete output list (payments + OP_RETURN + candidate change) before computing `vbytes`, `needed_fee`, the `TooLowFee` check, and the `MAX_STANDARD_TX_WEIGHT` check.

### Proof of Concept
Conceptual: call `SignableTransaction::new(inputs, payments, Some(change), Some(vec![0u8; 80]), fee_per_vbyte)` where `input_sat - payment_sat - fee_with_change >= DUST`. The resulting `needed_fee()` equals `fee_per_vbyte * vbytes` where `vbytes` was measured on a transaction lacking the OP_RETURN output; `fee()` on the built transaction therefore corresponds to an effective rate `< fee_per_vbyte`. Choosing `fee_per_vbyte` so that the true rate is under `DEFAULT_MIN_RELAY_TX_FEE` while the underestimated `vbytes` still satisfies `needed_fee >= (DEFAULT_MIN_RELAY_TX_FEE * vbytes) / 1000` demonstrates the guard bypass.