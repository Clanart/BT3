# Q1795: Exploit CPFP fee-path selection in fill_in_utxo_info

## Question
Can an unprivileged attacker use public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction with crafted the `cancel_outpoints` / `cancel_txids` dependency lists so `fill_in_utxo_info` attaches CPFP state to the wrong parent, fee payer, or anchor, corrupting the binding between raw transaction bytes and the queued tx-sender metadata and breaking the invariant that database state must not say a send path is finalized / active when the raw transaction path says otherwise, leading to Critical. Direct loss of funds?

## Target
- File/function: crates/clementine-tx-sender/src/rbf.rs::fill_in_utxo_info
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `cancel_outpoints` / `cancel_txids` dependency lists
- Exploit idea: attach CPFP state to the wrong parent, anchor, or fee payer using the `cancel_outpoints` / `cancel_txids` dependency lists
- Invariant to test: database state must not say a send path is finalized / active when the raw transaction path says otherwise
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
