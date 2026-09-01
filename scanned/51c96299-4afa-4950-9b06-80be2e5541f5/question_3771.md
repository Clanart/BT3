# Q3771: `send_citrea_tx` and send-queue ordering/dependencies

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees cause `send_citrea_tx` in `crates/clementine-tx-sender/src/client.rs` to submit a bridge transaction before its parent or prerequisite is confirmed, or to drop one from the queue after an error, so a protocol stage is skipped and the associated UTXO is stranded?

## Target
- File/function: `crates/clementine-tx-sender/src/client.rs` -> `send_citrea_tx` (This module is provides a client which is responsible for inserting)
- Entrypoint: attacker-timed on-chain conditions -> `send_citrea_tx`
- Attacker controls: the on-chain conditions that make submissions fail or succeed; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: skip or strand a queued protocol transaction
- Invariant to test: every queued bridge transaction is eventually submitted after its prerequisites confirm
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: fail submissions adversarially and assert the queue still drains in order
