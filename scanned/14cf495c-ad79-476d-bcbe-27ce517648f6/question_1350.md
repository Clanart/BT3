# Q1350: `clear_citrea_commit_and_try_to_send_by_ids` and replacement of a bridge transaction

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees publish a conflicting transaction that `clear_citrea_commit_and_try_to_send_by_ids` in `crates/clementine-tx-sender/src/db/citrea.rs` treats as a replacement or as a confirmation of its own, so the bridge records a settlement or state transition against a transaction the attacker authored?

## Target
- File/function: `crates/clementine-tx-sender/src/db/citrea.rs` -> `clear_citrea_commit_and_try_to_send_by_ids` (Citrea-specific SQLx queries for tx-sender tables)
- Entrypoint: a conflicting Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `clear_citrea_commit_and_try_to_send_by_ids`
- Attacker controls: the conflicting transaction's inputs, outputs and fee; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: substitute an attacker transaction for a bridge transaction in the bridge's own bookkeeping
- Invariant to test: a transaction the bridge treats as its own == a transaction the bridge signed and submitted
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: broadcast a conflicting spend and assert `clear_citrea_commit_and_try_to_send_by_ids` does not adopt it
