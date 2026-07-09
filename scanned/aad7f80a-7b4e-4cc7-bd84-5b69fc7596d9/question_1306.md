# Q1306: NEAR UTXO connector withdrawal coupling one inbound event spawns multiple outbound obligations at boundary values

## Question
Can an unprivileged attacker trigger `public UTXO-origin forward path plus downstream connector use` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain and btc.rs` violate `UTXO-origin state must not let one spendable output drive both a direct payout and a second connector withdrawal obligation` in the `one inbound event spawns multiple outbound obligations` attack class because recomposes a UTXO-origin transfer into another bridge leg and eventually interacts with chain-specific UTXO connector behavior becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain and btc.rs`
- Entrypoint: `public UTXO-origin forward path plus downstream connector use`
- Attacker controls: UTXO transfer id, output set, relayer fee, and destination-chain token assumptions
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: UTXO-origin state must not let one spendable output drive both a direct payout and a second connector withdrawal obligation
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
