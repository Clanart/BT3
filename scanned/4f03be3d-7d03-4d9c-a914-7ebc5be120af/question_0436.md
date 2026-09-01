# Q0436: `create_txhandlers` and reorg handling

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees exploit how `create_txhandlers` in `core/src/states/context.rs` handles a reorg - state advanced on a block later orphaned, an event processed twice across the rollback, or a height/hash cached across the reorg - so the bridge's view of which bridge transactions are confirmed diverges permanently from Bitcoin's?

## Target
- File/function: `core/src/states/context.rs` -> `create_txhandlers`
- Entrypoint: a Bitcoin reorg the attacker can influence by transaction placement -> `create_txhandlers`
- Attacker controls: which of the attacker's transactions land in which branch, and their fees; attacker is an unprivileged party who can broadcast Bitcoin transactions and pay fees; holds no protocol role or key
- Exploit idea: permanently desynchronise bridge state from the chain
- Invariant to test: after any reorg, bridge state reflects exactly the transactions in the active chain
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: regtest: invalidate and rebuild blocks around `create_txhandlers` and assert state converges
