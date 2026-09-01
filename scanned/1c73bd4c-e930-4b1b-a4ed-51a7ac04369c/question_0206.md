# Q0206: `build_and_sign_child_tx` and replacement of a bridge transaction

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees publish a conflicting transaction that `build_and_sign_child_tx` in `crates/clementine-tx-sender/src/cpfp.rs` treats as a replacement or as a confirmation of its own, so the bridge records a settlement or state transition against a transaction the attacker authored?

## Target
- File/function: `crates/clementine-tx-sender/src/cpfp.rs` -> `build_and_sign_child_tx` (This module implements the Child Pays For Parent (CPFP) strategy for sending)
- Entrypoint: a conflicting Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `build_and_sign_child_tx`
- Attacker controls: the conflicting transaction's inputs, outputs and fee; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: substitute an attacker transaction for a bridge transaction in the bridge's own bookkeeping
- Invariant to test: a transaction the bridge treats as its own == a transaction the bridge signed and submitted
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: broadcast a conflicting spend and assert `build_and_sign_child_tx` does not adopt it
