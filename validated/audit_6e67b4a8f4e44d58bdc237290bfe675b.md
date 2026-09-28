### Title
Attacker dust-spams the scanned multisig P2TR script, forcing unprofitable/unspendable inputs and DoS-ing spends - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The Beanstalk report describes an unbounded array that an attacker can grow by transferring 1-wei plots to a victim, making the victim's own removals revert out-of-gas. The analog in Serai's Bitcoin wallet is the per-input cost model: `Scanner::scan_transaction` treats *every* output paying a registered `script_pubkey` as a `ReceivedOutput` with no value/dust filtering, and `SignableTransaction::new`/`TransactionMachine` require one full FROST signing pipeline per input. An unprivileged attacker who sends dust outputs to the public multisig address inflates the spendable-input set with outputs whose spend cost exceeds their value, and forces any consolidation to hit `MAX_STANDARD_TX_WEIGHT`, leaving reported funds economically unspendable.

### Finding Description
`Scanner::new` registers the base P2TR `script_pubkey` for the group key, and `scan_transaction` pushes a `ReceivedOutput` for *any* transaction output whose `script_pubkey` matches — with no minimum-value check:

```rust
// networks/bitcoin/src/wallet/mod.rs:199-214
pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
  let mut res = Vec::new();
  for (vout, output) in tx.output.iter().enumerate() {
    let Ok(vout) = u32::try_from(vout) else { break };
    if let Some(offset) = self.scripts.get(&output.script_pubkey) {
      res.push(ReceivedOutput { ... });
    }
  }
  res
}
```

On the spend side, `SignableTransaction::new` enforces `DUST = 546` only on *payments* (`send.rs:165-169`), never on inputs (`send.rs:175-185`). Every input is committed to via `Prevouts::All` and requires its own `AlgorithmMachine`/sighash signature (`send.rs:273-285`, `send.rs:373-397`). If the input set grows past `MAX_STANDARD_TX_WEIGHT`, construction fails with `TooLargeTransaction` (`send.rs:241-243`). There is no input-selection or consolidation path that avoids a per-input FROST signature — each dust input costs ~57+ vbytes of signature/witness weight and a full threshold-signing round.

### Impact Explanation
- **Funds reported received that are not spendable**: a dust output of 546 sats requires ~57 vbytes of witness/input weight to spend; at fee rates above ~9.5 sat/vB, spending it costs more than its value. The scanner reports it as a received balance regardless, and `SignableTransaction` will include it in `input_sat` accounting (`send.rs:175`), so reported balances overstate spendable funds and actual spends overpay fees.
- **DoS vector**: an attacker can create arbitrarily many dust outputs to the known public script (one transaction can carry hundreds of outputs to the same address). Consolidating them requires N parallel FROST signing machines (`multisig()` at `send.rs:273-285`) and N per-input sighash signatures, and beyond ~thousands of inputs the transaction exceeds `MAX_STANDARD_TX_WEIGHT` and cannot be constructed at all for a single spend. This mirrors the plot-index DoS: attacker-poisoned state makes the victim's own operation expensive/infeasible.

### Likelihood Explanation
The attack requires only sending Bitcoin transactions to the multisig's publicly known P2TR address — the exact public-input channel allowed (Bitcoin transactions an unprivileged party sends). No validator status, collusion, or key material is needed. The attacker does pay dust value + fees, but the griefing asymmetry persists: each ~546-sat output forces the threshold set to run a per-input signing machine and permanently inflates the wallet's tracked output set, and the wallet has no mechanism to reject or ignore dust-valued receipts.

### Recommendation
Apply a minimum-value check to scanned inputs mirroring the existing `DUST` guard — e.g., have `Scanner`/`SignableTransaction::new` ignore or explicitly reject inputs whose value is below the fee-cost to spend them at the target `fee_per_vbyte`, and/or implement coin selection that excludes unprofitable inputs rather than treating every matched output as spendable.

### Proof of Concept
1. Observe the multisig's P2TR `script_pubkey` (public on-chain).
2. Send one Bitcoin transaction with hundreds of 546-sat outputs to that script.
3. `Scanner::scan_transaction` returns a `ReceivedOutput` for each, all with `offset = Scalar::ZERO`.
4. Any attempt to spend them via `SignableTransaction::new(inputs, ...)` requires one `AlgorithmMachine` per input in `multisig()` (`send.rs:273-285`) and one `taproot_key_spend_signature_hash` per input (`send.rs:373-397`); with enough dust inputs, `weight > MAX_STANDARD_TX_WEIGHT` returns `TooLargeTransaction` (`send.rs:241-243`), and at any realistic fee rate the per-input signature weight makes each dust output cost more than 546 sats to move — funds the scanner reports as received but which cannot be economically spent.