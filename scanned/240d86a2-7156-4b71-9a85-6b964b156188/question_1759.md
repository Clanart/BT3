# Q1759: Exploit CPFP fee-path selection in create_child_tx

## Question
Can an unprivileged attacker use public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction with crafted the `cancel_outpoints` / `cancel_txids` dependency lists so `create_child_tx` attaches CPFP state to the wrong parent, fee payer, or anchor, corrupting the cancel/activate dependency graph persisted in the database and breaking the invariant that queued tx metadata, fee paths, and dependency sets must all describe the same transaction intent, leading to Critical. Direct loss of funds?

## Target
- File/function: crates/clementine-tx-sender/src/cpfp.rs::create_child_tx
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `cancel_outpoints` / `cancel_txids` dependency lists
- Exploit idea: attach CPFP state to the wrong parent, anchor, or fee payer using the `cancel_outpoints` / `cancel_txids` dependency lists
- Invariant to test: queued tx metadata, fee paths, and dependency sets must all describe the same transaction intent
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
