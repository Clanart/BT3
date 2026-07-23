# Q1794: Exploit CPFP fee-path selection in calculate_bump_feerate_if_needed

## Question
Can an unprivileged attacker use public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction with crafted the `activate_outpoints` / `activate_txids` dependency lists so `calculate_bump_feerate_if_needed` attaches CPFP state to the wrong parent, fee payer, or anchor, corrupting the finalized/seen-at-height status recorded for the send request and breaking the invariant that database state must not say a send path is finalized / active when the raw transaction path says otherwise, leading to Critical. Direct loss of funds?

## Target
- File/function: crates/clementine-tx-sender/src/rbf.rs::calculate_bump_feerate_if_needed
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `activate_outpoints` / `activate_txids` dependency lists
- Exploit idea: attach CPFP state to the wrong parent, anchor, or fee payer using the `activate_outpoints` / `activate_txids` dependency lists
- Invariant to test: database state must not say a send path is finalized / active when the raw transaction path says otherwise
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
