# Q3679: Mis-select UTXOs in try_to_send_unconfirmed_txs

## Question
Can an unprivileged attacker influence the `rbf_signing_info` object through public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction so `try_to_send_unconfirmed_txs` spends, cancels, or activates the wrong UTXO set, corrupting the cancel/activate dependency graph persisted in the database and breaking the invariant that queued tx metadata, fee paths, and dependency sets must all describe the same transaction intent, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: crates/clementine-tx-sender/src/lib.rs::try_to_send_unconfirmed_txs
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `rbf_signing_info` object
- Exploit idea: steer cancellation, activation, or fee selection toward the wrong spend set via the `rbf_signing_info` object
- Invariant to test: queued tx metadata, fee paths, and dependency sets must all describe the same transaction intent
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
