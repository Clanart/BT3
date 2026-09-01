# Q0607: `load_kickoff_machines` and transaction boundaries around on-chain actions

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port time an on-chain event so `load_kickoff_machines` in `core/src/database/state_machine.rs` commits part of a state change while the broadcast that must accompany it fails (or the reverse), leaving bookkeeping and the chain permanently inconsistent for a deposit?

## Target
- File/function: `core/src/database/state_machine.rs` -> `load_kickoff_machines` (This module includes database functions for persisting and loading state machines)
- Entrypoint: attacker-timed on-chain events -> `load_kickoff_machines`
- Attacker controls: the timing that makes one half of the operation fail; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: split an atomic protocol step
- Invariant to test: a state change and its on-chain effect are committed atomically or both rolled back
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: inject failures between the two halves and assert consistency
