### Title
Attacker-injected dust "donations" to the multisig are aggregated unconditionally, forcing the wallet to pay fees exceeding the dust's value - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The M-10 report's core class is that a protocol computes accounting over *externally manipulable* balance (raw `balanceOf`) instead of tracked contributions, letting an attacker "donate" value that distorts the computation to others' detriment. Serai's Bitcoin wallet has the same shape: `Scanner::scan_transaction` credits *any* output paying a registered script as a `ReceivedOutput` regardless of amount, and `SignableTransaction::new` sums all provided inputs into `input_sat` and pays weight-based fees for every input. An unprivileged attacker who knows the deposit `script_pubkey` (publicly visible on-chain) can send dust outputs below the spend-cost threshold; the wallet's accounting treats them as ordinary received funds, and spending them costs more in fees than they contribute.

### Finding Description
`Scanner::scan_transaction` (networks/bitcoin/src/wallet/mod.rs:199-214) matches solely on `output.script_pubkey` and records the output unconditionally — there is no minimum-value check:

```rust
if let Some(offset) = self.scripts.get(&output.script_pubkey) {
  res.push(ReceivedOutput { offset: *offset, output: output.clone(), ... });
}
```

`SignableTransaction::new` (networks/bitcoin/src/wallet/send.rs:150-256) then:

- sums all inputs into `input_sat` (line 175) and builds one `TxIn` per `ReceivedOutput` (lines 177-185),
- computes `needed_fee = fee_per_vbyte * vbytes` over the total input count (lines 204-206), so each attacker-injected input adds ~57.5 vbytes of fee obligation,
- enforces dust only on *payments* (`*amount < DUST`, lines 165-169), never on inputs,
- defines the effective fee as `sum(inputs) - sum(outputs)` (`fee()`, lines 138-141), so any shortfall from dust inputs is silently absorbed by reducing change.

Each P2TR input costs roughly `fee_per_vbyte * ~57.5` sats to spend, while the `DUST` floor is only 546 sats and inputs below it are still accepted. An attacker can craft outputs (e.g. exactly 546 sats, or even less via non-standard-but-consensus-valid values when relayed) whose fee cost exceeds their value. Because deposit addresses are derived from `register_offset` scripts that are publicly known once used, the attacker needs no privilege — they just send normal Bitcoin transactions.

Additionally, `scan_block` (mod.rs:221-227) scans the coinbase transaction, crediting immature outputs as if spendable — a miner can donate outputs that are reported as received but cannot be spent for 100 blocks, another instance of the same "untracked external balance treated as available" class.

### Impact Explanation
For every dust input injected, the multisig's transaction pays `fee_per_vbyte * input_vbytes` in fees funded by the aggregate `input_sat`. When `input_sat - payment_sat - fee_with_change < DUST`, the change output is dropped entirely (send.rs:228-234) and the remainder is burned as extra fee. A persistent attacker forces value destruction proportional to `fee_cost_per_input - dust_value` on every aggregation transaction, and can push transactions toward `MAX_STANDARD_TX_WEIGHT` (send.rs:241-243), causing `TooLargeTransaction` failures that stall withdrawals — a griefing/DoS on top of direct fee drain. The coinbase path additionally means "funds reported received that are not spendable" for ~100 blocks.

### Likelihood Explanation
High reachability: the attack requires only sending ordinary Bitcoin transactions to an address whose `script_pubkey` is observable on-chain after first use (or predictable if the offset derivation is known). No validator collusion, no leaked key, no malformed encoding is needed — the attacker exploits only the fact that scanning and transaction construction trust externally-injected outputs without a value floor. Profitability is negative for the attacker (it costs them the dust), so this is griefing/DoS rather than theft, consistent with a Medium severity.

### Recommendation
- In `Scanner::scan_transaction` (or at output registration in the scheduler), drop `ReceivedOutput`s whose `value` is below the marginal spend cost: `output.value.to_sat() >= fee_per_vbyte * P2TR_INPUT_VBYTES` (or at minimum `DUST`), so donations below spend-cost are not aggregated.
- Skip `block.txdata[0]` in `scan_block`, or tag coinbase-origin `ReceivedOutput`s so callers can enforce maturity before use.
- In `SignableTransaction::new`, charge the per-input marginal fee against each input's value rather than only against the aggregate, so uneconomical inputs cannot be subsidized by the rest of the input set.

### Proof of Concept
1. Observe the multisig's deposit `script_pubkey` on-chain (any previously used offset script is sufficient; `Scanner` keys scripts by `script_pubkey` only, mod.rs:205).
2. Send `k` transactions each creating an output of `546` sats (`DUST`) paying that script. Each is consensus-valid and standard-relayable.
3. On the next aggregation, `scan_block`/`scan_transaction` returns all `k` outputs as `ReceivedOutput`s (mod.rs:199-214).
4. The scheduler calls `SignableTransaction::new` with these inputs; `calculate_weight_vbytes` adds ~57.5 vbytes per input (send.rs:71-84, 204), so at `fee_per_vbyte = 20`, each 546-sat input obligates ~1150 sats of fee — a net loss of ~604 sats each, drawn from legitimate depositors' funds via reduced change or increased `fee()` (send.rs:138-141).
5. With enough inputs, `weight > MAX_STANDARD_TX_WEIGHT` triggers `TooLargeTransaction` (send.rs:241-243), blocking the transaction entirely until the dust is filtered — which the code never does.

Uncertainty note: whether the processor's scheduler actually aggregates *all* scanned outputs into one transaction lives outside the in-scope `networks/bitcoin/src` files, but the wallet-level primitives unconditionally accept and charge for every injected input, which is sufficient for the analog to stand on this code.