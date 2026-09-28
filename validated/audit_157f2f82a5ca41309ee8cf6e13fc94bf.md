### Title
Attacker-flooded small outputs permanently exceed `MAX_STANDARD_TX_WEIGHT`, making every spend plan unbuildable and freezing vault funds - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The external report's bug class is "an unprivileged attacker engineers on-chain/accounting state such that a necessary protocol operation reverts, permanently blocking recovery of funds." In Serai's Bitcoin network code, the direct analog is `SignableTransaction::new`: anyone can send Bitcoin transactions to the multisig's publicly derivable addresses (`External` with offset `Scalar::ZERO`, plus `Branch`, `Change`, and `Forwarded` offsets). The `Scanner` (`scan_transaction`) accepts every output paying to a registered `script_pubkey` with no per-sender or count limits, and the processor-side filter only discards outputs below `N::DUST`. Each accepted output becomes a `ReceivedOutput` that must be consumed as an input. Once enough outputs accumulate, any transaction spending them exceeds `MAX_STANDARD_TX_WEIGHT`, `SignableTransaction::new` returns `TooLargeTransaction`, and no spend (payment, forwarding, refund, or rotation/consolidation to a new key set) can be constructed — the vault's funds are received but unspendable.

### Finding Description
`Scanner::scan_transaction` at `networks/bitcoin/src/wallet/mod.rs:199-214` returns a `ReceivedOutput` for every transaction output whose `script_pubkey` matches a registered script, with no bound on count and no authentication of the sender. The only gating is the caller-side dust filter (`output.balance().amount.0 >= N::DUST`, `DUST = 546` at `send.rs:32`).

`SignableTransaction::new` then unconditionally places every supplied input into a single transaction (`tx_ins` built at `send.rs:177-185`), computes weight over all inputs at `send.rs:204`, and rejects the whole transaction at `send.rs:241-243`:

```rust
if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
  Err(TransactionError::TooLargeTransaction)?;
}
```

There is no mechanism to split inputs across multiple transactions, and each received output's `offset`/`outpoint` ties it to a required input. Additionally, `SignableTransaction::new` uses a single `fee_per_vbyte` model and all-or-nothing construction (`send.rs:215-235`), so a build failure is total rather than partial — mirroring the report's "liquidation always reverts" shape, where the failure mode is an unconditional hard error rather than degraded execution.

A P2TR key-spend input is ~57.5 vbytes, so roughly ~6,700 attacker-created outputs of ≥546 sat each (≈0.037 BTC total cost) push any consolidation transaction over `MAX_STANDARD_TX_WEIGHT` (400,000 WU). Because the attacker controls only public inputs (transactions they broadcast), this satisfies reachability: no validator, collusion, or leaked-key assumptions are needed.

### Impact Explanation
All funds held by the multisig become unspendable: `SignableTransaction::new` fails for any plan that must include the accumulated inputs, so payments, refunds (`Plan::Refund` consumption), forwards, and key-rotation consolidation transactions cannot be produced. This is the "funds reported received that are not spendable" impact — the `Scanner` reports them as received outputs, yet the wallet layer can never build a valid spend. For a threshold vault, this is permanent loss/bad-debt-equivalent: the Bitcoin cannot be moved by any honest quorum, analogous to the report's irrecoverable-collateral/bad-debt outcome.

### Likelihood Explanation
The attack requires only broadcasting standard Bitcoin transactions to an address anyone can derive (the external deposit address is intentionally public). The cost is bounded (~thousands of dollars in dust outputs plus fees) and independent of the vault's balance, so it is profitable to grief whenever the vault holds more than the attack cost. No trigger timing or privileged position is needed; success is deterministic once enough outputs exist on-chain. Likelihood is moderate-to-high given the deterministic failure and low barrier.

### Recommendation
- Bound the number of inputs per `SignableTransaction` and support spending a subset of `inputs` (add an input-selection/batching API so large UTXO sets are consumed across multiple transactions rather than failing wholesale).
- In `Scanner::scan_transaction` / the caller, raise the effective acceptance floor above `DUST` (e.g., only report outputs whose value meaningfully exceeds the marginal fee cost of spending them, ~`input_vbytes * fee_rate`), so sub-economic outputs are never recorded.
- Optionally distinguish depositor-provided outputs from unsolicited ones (e.g., require an `InInstruction`/registration for `External` outputs) so griefing outputs are ignored rather than queued as mandatory inputs.

### Proof of Concept
Conceptual reproduction (regtest):

1. Derive the multisig external address: `p2tr_script_buf(group_key)` (offset `Scalar::ZERO`, always registered in `Scanner::new`, `mod.rs:162-166`).
2. Broadcast transactions creating ~7,000 outputs of 546 sat each to that script. `scan_transaction` (`mod.rs:205-211`) returns each as a `ReceivedOutput`; all pass the `>= N::DUST` filter.
3. Build any spend: `SignableTransaction::new(all_outputs, payments, change, None, fee)` — `calculate_weight_vbytes` (`send.rs:62-127`) yields weight > 400,000 WU for ~7,000 inputs, and `send.rs:241-243` returns `TransactionError::TooLargeTransaction`.
4. Repeat with any honest payment/rotation attempt — construction fails unconditionally, so no threshold signature can ever move the funds.