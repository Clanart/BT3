# Q3115: `from_block` and block-cache lookups

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees construct a block whose contents make `from_block` in `core/src/builder/block_cache.rs` resolve a txid to the wrong transaction, miss a transaction it must see, or index outside the cached block (duplicate txids across blocks, a txid also appearing as a spend, an empty cache path), so bridge bookkeeping records a transaction that is not the one on chain?

## Target
- File/function: `core/src/builder/block_cache.rs` -> `from_block`
- Entrypoint: a Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `from_block`
- Attacker controls: which transactions the attacker places in the block and in what order; attacker is an unprivileged party who can broadcast Bitcoin transactions and pay fees; holds no protocol role or key
- Exploit idea: make bookkeeping point at the wrong transaction
- Invariant to test: every transaction the state machine records == the transaction at that position in the confirmed block
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: build the adversarial block in regtest and assert lookups resolve correctly
