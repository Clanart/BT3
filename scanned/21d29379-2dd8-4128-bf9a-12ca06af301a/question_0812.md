# Q0812: `on_dispatch` and duplicate event delivery

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees cause the same on-chain event to be delivered twice to `on_dispatch` in `core/src/states/round.rs` - across a restart, a rollback of the enclosing database transaction, or a re-scan of an already-processed height - so a single-use protocol resource (a kickoff connector, a settlement record, a signature) is consumed twice?

## Target
- File/function: `core/src/states/round.rs` -> `on_dispatch`
- Entrypoint: a Bitcoin transaction plus attacker-timed reorg/restart conditions -> `on_dispatch`
- Attacker controls: transaction placement and the timing that triggers re-scanning; attacker is an unprivileged party who can broadcast Bitcoin transactions and pay fees; holds no protocol role or key
- Exploit idea: consume a single-use resource twice via event replay
- Invariant to test: processing the same event twice leaves state identical to processing it once
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: deliver the event twice and assert idempotence
