# Q3670: Mis-select UTXOs in delete_try_to_send_tx

## Question
Can an unprivileged attacker influence the `fee_paying_type` choice through public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction so `delete_try_to_send_tx` spends, cancels, or activates the wrong UTXO set, corrupting the binding between raw transaction bytes and the queued tx-sender metadata and breaking the invariant that database state must not say a send path is finalized / active when the raw transaction path says otherwise, leading to Critical. Direct loss of funds?

## Target
- File/function: crates/clementine-tx-sender/src/db/tx_sender.rs::delete_try_to_send_tx
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `fee_paying_type` choice
- Exploit idea: steer cancellation, activation, or fee selection toward the wrong spend set via the `fee_paying_type` choice
- Invariant to test: database state must not say a send path is finalized / active when the raw transaction path says otherwise
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
