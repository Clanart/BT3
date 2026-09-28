### Title
`SignableTransaction::new` computes the fee on a transaction that excludes the already-added OP_RETURN data output, underpaying the intended fee rate - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the Kelp rsETH mint bug — where a deposit was credited to the state (`balanceOf`) before the mint amount was priced, so the user was charged against a denominator inflated by their own contribution — `SignableTransaction::new` mutates the transaction's output list with the attacker-influenced OP_RETURN `data` output *before* measuring the transaction for fee purposes, yet the measurement function is then invoked on `payments` (the pre-mutation output set), not on the mutated `tx_outs`. The result is a `needed_fee` computed for a smaller transaction than the one actually signed and broadcast.

### Finding Description
In `SignableTransaction::new`, the data output is appended to `tx_outs` first:

```rust
// Add the OP_RETURN output
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}

let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

`calculate_weight_vbytes` reconstructs a candidate `Transaction` from `payments` — not from `tx_outs` — so any OP_RETURN output present in `tx_outs` is absent from the measured transaction (`send.rs` lines 194–204, and the reconstruction at lines 85–94). The same omission occurs in the change branch, which again passes `payments` rather than `tx_outs` (lines 225–227). `data` of up to 80 bytes is permitted (checked at lines 171–173), adding an ~11–90+ byte output whose weight is never accounted for.

The final `SignableTransaction` is built with `output: tx_outs` (line 250) — i.e., it *does* contain the OP_RETURN — while `needed_fee` reflects a transaction without it. The `TooLowFee` minimum-relay check at lines 211–213 is likewise evaluated against the understated `vbytes`, so it cannot catch the underpayment.

### Impact Explanation
The signed transaction's real fee is `sum(inputs) - sum(outputs)` (`fee()`, lines 138–141). Because the OP_RETURN output is zero-valued, the absolute fee paid equals `needed_fee`, but the actual vsize is larger than `vbytes` — so the effective fee rate is strictly below the requested `fee_per_vbyte`. With a maximal 80-byte payload the shortfall is ~80–95 vbytes × `fee_per_vbyte` sats of rate. If `fee_per_vbyte` was chosen near the minimum relay rate, the real transaction can fall below `DEFAULT_MIN_RELAY_TX_FEE` and be rejected by relay policy; even when relayed it confirms at a lower rate than intended. Inputs use `Sequence::MAX` (line 182), so BIP-125 RBF is not signaled and a stuck/underpriced TX cannot be bumped by replacement — the multisig's funds are locked in an output that cannot be spent as planned. This is reachable by an unprivileged party: `data` is untrusted bytes embedded in the payment instruction that drives `SignableTransaction::new`, exactly paralleling how the Kelp attacker's deposit amount poisoned the price computation.

### Likelihood Explanation
Any schedule/payment carrying data (the InInstruction flow) triggers the miscalculation deterministically — no race or special state is needed. Whether the shortfall is fatal depends on how close the configured fee rate is to the relay minimum; at minimum-fee rates it deterministically produces a non-standard/underpriced transaction. Severity: Medium — a systematic fee miscalculation with a fund-locking edge case, not direct theft.

### Recommendation
Compute the measured output set from the actual outputs that will be broadcast. Either pass `tx_outs` (including the OP_RETURN) into `calculate_weight_vbytes`, or push the data output only after measurement is complete — mirroring the Kelp fix of ordering the mutation after the computation. The change branch should likewise measure `tx_outs + change` rather than `payments + change`:

```diff
- let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
+ let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), &tx_outs_as_scripts, None);
```

(equivalently, restructure `calculate_weight_vbytes` to take the final `Vec<TxOut>`).

### Proof of Concept
```rust
// inputs: one ReceivedOutput of value V
// payments: [(script, DUST)]
// data: Some(vec![0xaa; 80])  // 80-byte OP_RETURN payload
// fee_per_vbyte: chosen so needed_fee == DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000

let tx = SignableTransaction::new(inputs, &payments, None, Some(data), fee_per_vbyte).unwrap();
// tx.transaction().output contains the OP_RETURN (tx_outs included it)
// but tx.needed_fee() == fee_per_vbyte * vbytes(payments only), missing ~80-95 vbytes
// => actual fee rate = needed_fee / vsize(signed tx) < DEFAULT_MIN_RELAY_TX_FEE per kvbyte
// => tx fails relay/policy or confirms far below the intended rate
```

Root cause location: `networks/bitcoin/src/wallet/send.rs` — data pushed to `tx_outs` at lines 194–202; measurement over `payments` (not `tx_outs`) at lines 204 and 225–227; final TX built from the unmeasured `tx_outs` at line 250.