# Q3603: `send_cpfp_tx` and replacement of a bridge transaction

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees publish a conflicting transaction that `send_cpfp_tx` in `crates/clementine-tx-sender/src/cpfp.rs` treats as a replacement or as a confirmation of its own, so the bridge records a settlement or state transition against a transaction the attacker authored?

## Target
- File/function: `crates/clementine-tx-sender/src/cpfp.rs` -> `send_cpfp_tx` (This module implements the Child Pays For Parent (CPFP) strategy for sending)
- Entrypoint: a conflicting Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `send_cpfp_tx`
- Attacker controls: the conflicting transaction's inputs, outputs and fee; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: substitute an attacker transaction for a bridge transaction in the bridge's own bookkeeping
- Invariant to test: a transaction the bridge treats as its own == a transaction the bridge signed and submitted
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: broadcast a conflicting spend and assert `send_cpfp_tx` does not adopt it
