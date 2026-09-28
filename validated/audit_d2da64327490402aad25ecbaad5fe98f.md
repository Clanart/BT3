### Title
OP_RETURN data output added after fee/weight calculation, so its size is never paid for - (`networks/bitcoin/src/wallet/send.rs`)

### Summary
In the Notional bug, a user's deposit was credited *after* the repayment step, so it could not be used for the repayment it was meant to fund — a pure ordering flaw. `SignableTransaction::new` in bitcoin-serai exhibits the same ordering flaw: the OP_RETURN output carrying user-supplied `data` is pushed into `tx_outs` before the weight/vbyte computation, but `calculate_weight_vbytes` is invoked with `payments` — a slice that does not include the OP_RETURN output. The data output's weight is therefore never accounted for in `needed_fee`, so the transaction systematically underpays relative to the requested `fee_per_vbyte`.

### Finding Description
`SignableTransaction::new` builds the output set in this order:

1. `tx_outs` is populated from `payments` (`send.rs:188-191`).
2. The OP_RETURN output with up to 80 bytes of attacker/user-supplied data is appended to `tx_outs` (`send.rs:194-202`).
3. The fee is then computed via `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` (`send.rs:204`), which builds a template transaction whose `output` list is derived **only from `payments`** (`send.rs:85-93`). The OP_RETURN output already present in `tx_outs` is not included.

The same omission occurs in the change path: `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (`send.rs:225-226`) again ignores the OP_RETURN output.

Concretely, `needed_fee = fee_per_vbyte * vbytes` (`send.rs:206`) where `vbytes` excludes the data output. For a maximally-sized `data` payload, the output adds roughly 91 vbytes (8-byte amount + compactsize script length + 1-byte OP_RETURN + push opcode + 80-byte payload), so the transaction can be ~91 vbytes larger than what was paid for. The minimum-relay-fee check (`send.rs:211`) and the `NotEnoughFunds` check (`send.rs:215`) are both evaluated against this understated `vbytes`. The actual fee is still `sum(inputs) - sum(outputs)` (`fee()`, `send.rs:138-141`), so the effective fee rate of the broadcast transaction is lower than `fee_per_vbyte` — exactly as if a "deposit" (the data output's cost) had been booked after the "repayment" (the fee calculation) that was supposed to cover it.

### Impact Explanation
An instruction carrying `data` produces a transaction whose real fee rate is below the rate the coordinator/scheduler agreed to pay. If `fee_per_vbyte` is at or near the relay minimum (the `TooLowFee` check at `send.rs:211` only enforces `DEFAULT_MIN_RELAY_TX_FEE` against the understated vbytes), the resulting transaction can fall below the mempool minimum relay fee and be rejected/burned by the network, leaving the multisig's funds unspendable via that signed transaction and forcing a re-signing round. More generally, every data-carrying send silently pays less fee than requested, degrading confirmation reliability for forwarding/refund transactions produced by the scheduler, which treat the signed TX as final (`networks/bitcoin/src/wallet/send.rs:373-397`).

### Likelihood Explanation
Reachable by an unprivileged external user: `data` originates from `OutInstruction`/InInstruction data embedded in external transactions, and any value `0 < len <= 80` triggers the miscalculation. The underpayment is deterministic (proportional to data length), not probabilistic. Impact is bounded — funds are not stolen, only delayed — matching the Medium severity of the source finding.

### Recommendation
Compute weight from the actual output set rather than `payments`. Change `calculate_weight_vbytes` to take the fully-built `tx_outs` (including the OP_RETURN output), or pass the serialized `data` length into it so the OP_RETURN output's size is included when deriving `vbytes`, `needed_fee`, the `TooLowFee` check, and the change-output residual calculation at `send.rs:228`. Add a regression test asserting `fee()` equals `needed_fee` when `data` is `Some`.

### Proof of Concept
In `networks/bitcoin/src/wallet/send.rs`:

```rust
// Line 194-202: OP_RETURN output is added to tx_outs first
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}

// Line 204: but the weight/fee calculation only sees `payments`, not `tx_outs`
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;   // excludes the OP_RETURN output
```

Inside `calculate_weight_vbytes` (`send.rs:85-93`), the template `tx.output` is built from `payments.iter()` only — `data` is never a parameter. Hence a 80-byte `data` payload inflates the real transaction by ~91 vbytes while `needed_fee` and the minimum-relay check remain based on the smaller size, so `fee()` / actual-vbytes < `fee_per_vbyte`.