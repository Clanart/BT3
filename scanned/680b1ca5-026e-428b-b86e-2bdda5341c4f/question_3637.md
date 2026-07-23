# Q3637: Mis-select UTXOs in list_unfinalized_cancel_outpoints

## Question
Can an unprivileged attacker influence the `tx_metadata` fields attached to the queued send request through public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction so `list_unfinalized_cancel_outpoints` spends, cancels, or activates the wrong UTXO set, corrupting the binding between raw transaction bytes and the queued tx-sender metadata and breaking the invariant that database state must not say a send path is finalized / active when the raw transaction path says otherwise, leading to Critical. Direct loss of funds?

## Target
- File/function: crates/clementine-tx-sender/src/db/tx_sender.rs::list_unfinalized_cancel_outpoints
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `tx_metadata` fields attached to the queued send request
- Exploit idea: steer cancellation, activation, or fee selection toward the wrong spend set via the `tx_metadata` fields attached to the queued send request
- Invariant to test: database state must not say a send path is finalized / active when the raw transaction path says otherwise
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
