# Q2746: `process_and_add_new_states_from_height` and the ordering of dispatched duties

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees shape a block containing several protocol-relevant transactions so the order `process_and_add_new_states_from_height` in `core/src/states/mod.rs` dispatches them differs from the order the protocol requires, causing a later-stage action to be taken before its precondition is on chain?

## Target
- File/function: `core/src/states/mod.rs` -> `process_and_add_new_states_from_height` (State manager module)
- Entrypoint: a Bitcoin block shaped by an unprivileged party paying only mining fees -> `process_and_add_new_states_from_height`
- Attacker controls: the set and ordering of transactions in the block; attacker is an unprivileged party who can broadcast Bitcoin transactions and pay fees; holds no protocol role or key
- Exploit idea: invert the order of protocol stages
- Invariant to test: duties are dispatched in an order consistent with the confirmed on-chain sequence
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: place transactions in adversarial order and assert dispatch order is canonical
