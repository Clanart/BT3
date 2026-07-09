# Q974: NEAR UTXO connector withdrawal coupling one inbound event spawns multiple outbound obligations via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public UTXO-origin forward path plus downstream connector use` and then replay or reorder the complementary outbound or inbound bridge leg so that `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain and btc.rs` ends up accepting two inconsistent interpretations of the same economic event specifically around `one inbound event spawns multiple outbound obligations` under recomposes a UTXO-origin transfer into another bridge leg and eventually interacts with chain-specific UTXO connector behavior, violating `UTXO-origin state must not let one spendable output drive both a direct payout and a second connector withdrawal obligation`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain and btc.rs`
- Entrypoint: `public UTXO-origin forward path plus downstream connector use`
- Attacker controls: UTXO transfer id, output set, relayer fee, and destination-chain token assumptions
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: UTXO-origin state must not let one spendable output drive both a direct payout and a second connector withdrawal obligation
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims. Then replay or reorder the complementary outbound or inbound bridge leg and assert that the bridge still exposes only one valid economic outcome.
