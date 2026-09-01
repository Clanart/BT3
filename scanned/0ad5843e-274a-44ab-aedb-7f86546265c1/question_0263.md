# Q0263: `get_latest_proven_block_info_until_height` and rollback-induced replay

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port trigger a rollback of the transaction enclosing `get_latest_proven_block_info_until_height` in `core/src/database/header_chain_prover.rs` (via a later failure in the same unit of work) after an on-chain broadcast has already happened, so the work is retried and a second on-chain action is taken for one event?

## Target
- File/function: `core/src/database/header_chain_prover.rs` -> `get_latest_proven_block_info_until_height` (This module includes database functions which are mainly used by the header)
- Entrypoint: attacker-timed failures in the enclosing unit of work -> `get_latest_proven_block_info_until_height`
- Attacker controls: the on-chain condition that fails the later step; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: double-broadcast a protocol action through rollback and retry
- Invariant to test: retrying after a rollback produces no additional on-chain effect
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: fail the tail of the unit of work and assert no duplicate broadcast
