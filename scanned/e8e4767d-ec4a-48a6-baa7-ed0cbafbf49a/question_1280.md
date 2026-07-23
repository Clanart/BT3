# Q1280: Exploit replacement logic in set_activate_outpoint_finalized

## Question
Can an unprivileged attacker shape the `tx_metadata` fields attached to the queued send request so `set_activate_outpoint_finalized` constructs a replacement path that copies the wrong witnesses, inputs, or fee logic, corrupting the fee-payer UTXO chain selected for CPFP/RBF and violating the invariant that database state must not say a send path is finalized / active when the raw transaction path says otherwise, leading to Critical. Direct loss of funds?

## Target
- File/function: crates/clementine-tx-sender/src/db/tx_sender.rs::set_activate_outpoint_finalized
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `tx_metadata` fields attached to the queued send request
- Exploit idea: copy the wrong witnesses, inputs, or fee logic by shaping the `tx_metadata` fields attached to the queued send request
- Invariant to test: database state must not say a send path is finalized / active when the raw transaction path says otherwise
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
