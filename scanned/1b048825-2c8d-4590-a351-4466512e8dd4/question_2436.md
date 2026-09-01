# Q2436: `save_unproven_block_cache` and reorg handling

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees exploit how `save_unproven_block_cache` in `core/src/header_chain_prover.rs` handles a reorg - state advanced on a block later orphaned, an event processed twice across the rollback, or a height/hash cached across the reorg - so the bridge's view of which bridge transactions are confirmed diverges permanently from Bitcoin's?

## Target
- File/function: `core/src/header_chain_prover.rs` -> `save_unproven_block_cache` (This module contains utilities for proving Bitcoin block headers. This)
- Entrypoint: a Bitcoin reorg the attacker can influence by transaction placement -> `save_unproven_block_cache`
- Attacker controls: which of the attacker's transactions land in which branch, and their fees; attacker is an unprivileged party who can broadcast Bitcoin transactions and pay fees; holds no protocol role or key
- Exploit idea: permanently desynchronise bridge state from the chain
- Invariant to test: after any reorg, bridge state reflects exactly the transactions in the active chain
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: regtest: invalidate and rebuild blocks around `save_unproven_block_cache` and assert state converges
