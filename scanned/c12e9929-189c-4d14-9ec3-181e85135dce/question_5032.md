# Q5032: `is_kickoff_relevant_for_owner` and reorg handling

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees exploit how `is_kickoff_relevant_for_owner` in `core/src/states/context.rs` handles a reorg - state advanced on a block later orphaned, an event processed twice across the rollback, or a height/hash cached across the reorg - so the bridge's view of which bridge transactions are confirmed diverges permanently from Bitcoin's?

## Target
- File/function: `core/src/states/context.rs` -> `is_kickoff_relevant_for_owner`
- Entrypoint: a Bitcoin reorg the attacker can influence by transaction placement -> `is_kickoff_relevant_for_owner`
- Attacker controls: which of the attacker's transactions land in which branch, and their fees; attacker is an unprivileged party who can broadcast Bitcoin transactions and pay fees; holds no protocol role or key
- Exploit idea: permanently desynchronise bridge state from the chain
- Invariant to test: after any reorg, bridge state reflects exactly the transactions in the active chain
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: regtest: invalidate and rebuild blocks around `is_kickoff_relevant_for_owner` and assert state converges
