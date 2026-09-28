### Title
`SignableTransaction::new` omits the OP_RETURN data output from weight/vsize and fee calculation — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` builds `tx_outs` including a user-supplied OP_RETURN data output, but then computes the transaction's weight, virtual size, `needed_fee`, and the `MAX_STANDARD_TX_WEIGHT` bound from `payments` only — which excludes the data output. Any transaction carrying data underpays its fee (and can slip under the minimum relay fee check and the standardness weight cap), directly analogous to the Namada incident's accounting/view mismatch: the wallet believes the transaction costs `needed_fee` at `fee_per_vbyte`, while the actual on-chain transaction is larger and pays a lower effective feerate than the node requires.

### Finding Description
In `SignableTransaction::new`, the data output is appended to `tx_outs` before weight is measured:

```rust
// networks/bitcoin/src/wallet/send.rs:194-204
if let Some(data) = data {
  tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) });
}
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

`calculate_weight_vbytes` reconstructs a model transaction whose `output` list is built exclusively from `payments` (plus optional `change`); it never receives `tx_outs` or `data`. The same omission occurs on the change path at line 225-226. Consequences:

- `needed_fee = fee_per_vbyte * vbytes` undercharges by the OP_RETURN's vsize (≈ `11 + data.len()` bytes → up to ~91 extra bytes, ~364 weight units).
- The `TooLowFee` check (`DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`) is evaluated against the underestimated `vbytes`, so a transaction which in reality falls below the relay minimum can be accepted as valid.
- The `weight > MAX_STANDARD_TX_WEIGHT` check is also evaluated on the undercounted weight.
- `change` logic uses `fee_with_change` computed from the same underestimation, so change is slightly overpaid into existence or the dust decision is made on wrong numbers.
- `needed_fee()` is documented as "the fee necessary to achieve the fee rate specified at construction" — the actual fee paid (`fee() = inputs − outputs`) equals `needed_fee`, but the achieved feerate is strictly lower than `fee_per_vbyte` whenever `data.is_some()`.

In the in-scope processor path (`processor/src/networks/bitcoin.rs:446`), `data` is always `None`, so the production coordinator never emits the buggy transaction; the bug is fully reachable by any consumer of `bitcoin-serai`'s wallet API passing `Some(data)` with up to 80 bytes.

### Impact Explanation
A transaction carrying data pays a lower effective feerate than the caller specified and can fall below the node's minimum relay fee (`TooLowFee` was checked against a too-small vsize), producing transactions that are broadcast-rejected or stuck — funds appear committed/spendable per `needed_fee()` accounting but the transaction is not relayable at the claimed rate. In the worst case (large inputs + 80-byte data) the underestimation also weakens the `MAX_STANDARD_TX_WEIGHT` standardness guard, yielding non-standard transactions. Loss of availability of funds/DoS on spend, Medium severity at most; no theft.

### Likelihood Explanation
Triggered deterministically whenever `data` is `Some(_)`. The underpayment magnitude is bounded (~90 vbytes), so relay failure only materializes when `fee_per_vbyte` is at/near the relay floor — which is exactly the regime `TooLowFee` exists to catch, and the check is bypassed for the data-bearing case.

### Recommendation
Pass the fully constructed `tx_outs` (or `data`) into `calculate_weight_vbytes` so weight, vbytes, `needed_fee`, and the standardness check reflect the actual transaction. E.g. restructure `calculate_weight_vbytes` to accept the output list and compute `needed_fee` after `tx_outs` (including OP_RETURN and change) are finalized, then re-verify `TooLowFee` and `MAX_STANDARD_TX_WEIGHT` against the final weight.

### Proof of Concept
```rust
// Any caller: inputs with value V, one payment P, 80-byte data, fee_per_vbyte = 1 sat/vB
// vbytes is computed WITHOUT the OP_RETURN output.
let tx = SignableTransaction::new(inputs, &payments, None, Some(vec![0; 80]), 1).unwrap();
// tx.needed_fee() == 1 * vbytes_without_opreturn
// Actual tx.vsize() ≈ vbytes_without_opreturn + ~91
// Effective feerate = needed_fee / actual_vsize < 1 sat/vB,
// potentially < DEFAULT_MIN_RELAY_TX_FEE → node rejects on broadcast.
```
Compare `tx.transaction().vsize() * fee_per_vbyte` against `tx.needed_fee()` — they differ by the serialized size of the OP_RETURN output (≈ 9 + push overhead + `data.len()` bytes of base size), confirming the fee accounting mismatch.