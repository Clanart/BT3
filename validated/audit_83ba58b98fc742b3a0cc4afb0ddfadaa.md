### Title
`SignableTransaction` under-counts vsize when an OP_RETURN data output is present, producing transactions with an arbitrarily lower fee rate than requested — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` computes `needed_fee` by simulating a transaction built only from the declared `payments`, omitting the OP_RETURN output that carries caller-supplied `data` (up to 80 bytes) and, in the change path, recomputing with the same omission. Like the Curve swap that accepts `get_dy`'s manipulated output as its own `_min_dy` bound, the fee bound here is derived from a quantity the caller can shift: the actual serialized transaction is larger than the priced one, so the transaction silently pays a lower effective fee rate than `fee_per_vbyte` — potentially below the network minimum relay fee — while the code reports the wrong `needed_fee`.

### Finding Description
In `SignableTransaction::new` the OP_RETURN output is pushed onto `tx_outs` before the weight is ever calculated:

```rust
// send.rs:194-202
if let Some(data) = data {
  tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) })
}
// send.rs:204-206
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

`calculate_weight_vbytes` (`send.rs:62-127`) reconstructs a template transaction containing only `payments` (plus an optional `change` output). It never sees the `data` output, so `vbytes` excludes the OP_RETURN's ~9-byte `TxOut` overhead, its script of up to ~83 bytes, and any CompactSize length-field widening. The same omission occurs in the change path (`send.rs:224-233`), where `fee_with_change` is computed with `Some(&change)` but still without `data`.

All downstream decisions use the underestimated `needed_fee`/`vbytes`:
- `TooLowFee` compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` with the underestimated `vbytes` (`send.rs:211`).
- `NotEnoughFunds` is checked against `payment_sat + needed_fee` (`send.rs:215`).
- The change amount is `input_sat - payment_sat - fee_with_change` (`send.rs:228`), so the missing fee is silently absorbed into change — the transaction still signs, just with a lower real fee rate.
- `needed_fee()` (`send.rs:133`) reports the wrong figure to the caller, and `fee()` (`send.rs:138-141`) reveals the discrepancy only after the fact.

The signed transaction commits to the real (larger) outputs, so its actual feerate is `actual_fee / real_vsize < fee_per_vbyte`. With `data` near 80 bytes, the true transaction exceeds the priced one by roughly 100+ weight units (~25–95+ vbytes depending on push opcodes), enough to drop a marginally-priced transaction below `DEFAULT_MIN_RELAY_TX_FEE` or below the confirmation target.

### Impact Explanation
The multisig will sign and broadcast a transaction that either (a) pays less than the intended fee rate — degrading bridge withdrawal/refund reliability — or (b) fails mempool acceptance outright when the underestimated `TooLowFee` check passes but the real size pushes the effective rate under the relay minimum. Since `Prevouts::All` sighash commits to every input, an un-relayable or stuck transaction locks those UTXOs until the plan is rebuilt, and the reported `needed_fee` misstates what was actually paid. Funds the processor believes are being forwarded may sit unconfirmed or be treated as sent while effectively unspendable at the committed fee.

### Likelihood Explanation
`data` is a caller-controlled field (in Serai's flow it carries the `InInstruction`/out-instruction payload, which is influenced by external users' instructions and refunds). Any plan that attaches data to a Bitcoin transaction triggers the underestimation deterministically — no manipulation window or race needed. Exploitation requires only that a transaction be built with `data` and a fee rate close to the relay minimum or an economically meaningful margin.

### Recommendation
Include the data output in the size calculation: pass the full output set — payments plus the OP_RETURN `TxOut` — into `calculate_weight_vbytes` (e.g., build `tx_outs` first, including `data` and a placeholder-value change, and compute weight from that). Alternatively, add the serialized size of the OP_RETURN output to `vbytes`/`weight` before computing `needed_fee`, and recompute the `TooLowFee` and change checks against the corrected size.

### Proof of Concept
Construct `SignableTransaction::new(inputs, payments, change, Some(vec![0u8; 80]), fee_per_vbyte)` and compare `tx.transaction().vsize()` against `needed_fee() / fee_per_vbyte`:

```rust
let tx = SignableTransaction::new(
  vec![output], &payments, Some(change_script), Some(vec![0u8; 80]), FEE,
).unwrap();
// The actual signed transaction is larger than what was priced
assert!(tx.transaction().vsize() as u64 > tx.needed_fee() / FEE);
// The true fee rate is therefore below FEE:
// tx.fee() / tx.transaction().vsize() < FEE
```

With `fee_per_vbyte` chosen so `needed_fee` just clears `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`, the resulting transaction's real feerate falls under the relay minimum and is rejected by standard mempools despite `SignableTransaction` reporting success.

Note: I was unable to trace every upstream caller of `data` within the remaining iterations to fully confirm that arbitrary external users can force a near-minimal `fee_per_vbyte`; the undercount itself is unconditional whenever `data` is `Some`, but the practical severity depends on how aggressively the coordinator's fee selection hugs the relay floor.