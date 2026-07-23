# Q3681: Mis-select UTXOs in send_no_funding_tx

## Question
Can an unprivileged attacker influence the `activate_outpoints` / `activate_txids` dependency lists through public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction so `send_no_funding_tx` spends, cancels, or activates the wrong UTXO set, corrupting the fee-payer UTXO chain selected for CPFP/RBF and breaking the invariant that database state must not say a send path is finalized / active when the raw transaction path says otherwise, leading to Critical. Direct loss of funds?

## Target
- File/function: crates/clementine-tx-sender/src/lib.rs::send_no_funding_tx
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `activate_outpoints` / `activate_txids` dependency lists
- Exploit idea: steer cancellation, activation, or fee selection toward the wrong spend set via the `activate_outpoints` / `activate_txids` dependency lists
- Invariant to test: database state must not say a send path is finalized / active when the raw transaction path says otherwise
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
