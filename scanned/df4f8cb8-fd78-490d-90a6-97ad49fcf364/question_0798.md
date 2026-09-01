# Q0798: `update_synced_height` and confirmation detection

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees make `update_synced_height` in `crates/clementine-tx-sender/src/db/tx_sender.rs` believe a bridge transaction is confirmed when it is not (or the reverse) - a same-txid replacement, a reorged confirmation, a txid collision with an attacker transaction - so the protocol advances or stalls incorrectly?

## Target
- File/function: `crates/clementine-tx-sender/src/db/tx_sender.rs` -> `update_synced_height` (SQLx queries for tx-sender tables)
- Entrypoint: a Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `update_synced_height`
- Attacker controls: the attacker transaction's txid, placement and reorg exposure; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: desynchronise confirmation bookkeeping from the chain
- Invariant to test: a transaction is recorded confirmed iff it is in the active chain at the required depth
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: reorg a confirmation away and assert `update_synced_height` retracts it
