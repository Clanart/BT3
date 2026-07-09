# Q808: NEAR UTXO connector withdrawal coupling one inbound event spawns multiple outbound obligations

## Question
Can an unprivileged attacker settle through `public UTXO-origin forward path plus downstream connector use` and make `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain and btc.rs` both release local value and create a second valid outbound bridge obligation via recomposes a UTXO-origin transfer into another bridge leg and eventually interacts with chain-specific UTXO connector behavior, violating `UTXO-origin state must not let one spendable output drive both a direct payout and a second connector withdrawal obligation`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain and btc.rs`
- Entrypoint: `public UTXO-origin forward path plus downstream connector use`
- Attacker controls: UTXO transfer id, output set, relayer fee, and destination-chain token assumptions
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer.
- Invariant to test: UTXO-origin state must not let one spendable output drive both a direct payout and a second connector withdrawal obligation
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims.
