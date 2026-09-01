# Q2837: `on_ready_to_reimburse_entry` and state persisted across restarts

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees time an on-chain action against a restart or a failed database transaction so `on_ready_to_reimburse_entry` in `core/src/states/round.rs` resumes from state that skips or repeats a transition, leaving a bridge UTXO in a state with no reachable spend path?

## Target
- File/function: `core/src/states/round.rs` -> `on_ready_to_reimburse_entry`
- Entrypoint: attacker-timed Bitcoin transactions -> `on_ready_to_reimburse_entry`
- Attacker controls: transaction timing relative to the entity's processing; attacker is an unprivileged party who can broadcast Bitcoin transactions and pay fees; holds no protocol role or key
- Exploit idea: strand a bridge UTXO between states
- Invariant to test: for every persisted state, some sequence of protocol actions still spends the associated UTXO
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: kill and resume the state machine at each transition and assert reachability
