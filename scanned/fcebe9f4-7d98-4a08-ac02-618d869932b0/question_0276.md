# Q0276: `get_tx_of_utxo` and duplicate event delivery

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees cause the same on-chain event to be delivered twice to `get_tx_of_utxo` in `core/src/builder/block_cache.rs` - across a restart, a rollback of the enclosing database transaction, or a re-scan of an already-processed height - so a single-use protocol resource (a kickoff connector, a settlement record, a signature) is consumed twice?

## Target
- File/function: `core/src/builder/block_cache.rs` -> `get_tx_of_utxo`
- Entrypoint: a Bitcoin transaction plus attacker-timed reorg/restart conditions -> `get_tx_of_utxo`
- Attacker controls: transaction placement and the timing that triggers re-scanning; attacker is an unprivileged party who can broadcast Bitcoin transactions and pay fees; holds no protocol role or key
- Exploit idea: consume a single-use resource twice via event replay
- Invariant to test: processing the same event twice leaves state identical to processing it once
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: deliver the event twice and assert idempotence
