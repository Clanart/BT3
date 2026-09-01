# Q1839: `pgmq_queue_exists` and rollback-induced replay

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port trigger a rollback of the transaction enclosing `pgmq_queue_exists` in `core/src/database/state_machine.rs` (via a later failure in the same unit of work) after an on-chain broadcast has already happened, so the work is retried and a second on-chain action is taken for one event?

## Target
- File/function: `core/src/database/state_machine.rs` -> `pgmq_queue_exists` (This module includes database functions for persisting and loading state machines)
- Entrypoint: attacker-timed failures in the enclosing unit of work -> `pgmq_queue_exists`
- Attacker controls: the on-chain condition that fails the later step; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: double-broadcast a protocol action through rollback and retry
- Invariant to test: retrying after a rollback produces no additional on-chain effect
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: fail the tail of the unit of work and assert no duplicate broadcast
