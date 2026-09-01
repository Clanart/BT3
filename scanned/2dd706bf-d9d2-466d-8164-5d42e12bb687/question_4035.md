# Q4035: `local_addr` and confirmation detection

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees make `local_addr` in `crates/clementine-tx-sender/src/jsonrpc/server.rs` believe a bridge transaction is confirmed when it is not (or the reverse) - a same-txid replacement, a reorged confirmation, a txid collision with an attacker transaction - so the protocol advances or stalls incorrectly?

## Target
- File/function: `crates/clementine-tx-sender/src/jsonrpc/server.rs` -> `local_addr`
- Entrypoint: a Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `local_addr`
- Attacker controls: the attacker transaction's txid, placement and reorg exposure; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: desynchronise confirmation bookkeeping from the chain
- Invariant to test: a transaction is recorded confirmed iff it is in the active chain at the required depth
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: reorg a confirmation away and assert `local_addr` retracts it
