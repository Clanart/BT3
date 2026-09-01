# Q3315: `serialize_tx_for_fund_raw` and replacement of a bridge transaction

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees publish a conflicting transaction that `serialize_tx_for_fund_raw` in `crates/clementine-tx-sender/src/lib.rs` treats as a replacement or as a confirmation of its own, so the bridge records a settlement or state transition against a transaction the attacker authored?

## Target
- File/function: `crates/clementine-tx-sender/src/lib.rs` -> `serialize_tx_for_fund_raw` (This crate handles the creation, signing, and broadcasting of Bitcoin transactions,)
- Entrypoint: a conflicting Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `serialize_tx_for_fund_raw`
- Attacker controls: the conflicting transaction's inputs, outputs and fee; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: substitute an attacker transaction for a bridge transaction in the bridge's own bookkeeping
- Invariant to test: a transaction the bridge treats as its own == a transaction the bridge signed and submitted
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: broadcast a conflicting spend and assert `serialize_tx_for_fund_raw` does not adopt it
