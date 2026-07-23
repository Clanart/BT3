# Q1769: Exploit CPFP fee-path selection in mark_fee_payer_utxo_as_evicted

## Question
Can an unprivileged attacker use public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction with crafted the `fee_paying_type` choice so `mark_fee_payer_utxo_as_evicted` attaches CPFP state to the wrong parent, fee payer, or anchor, corrupting the fee-payer UTXO chain selected for CPFP/RBF and breaking the invariant that database state must not say a send path is finalized / active when the raw transaction path says otherwise, leading to Critical. Direct loss of funds?

## Target
- File/function: crates/clementine-tx-sender/src/db/tx_sender.rs::mark_fee_payer_utxo_as_evicted
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `fee_paying_type` choice
- Exploit idea: attach CPFP state to the wrong parent, anchor, or fee payer using the `fee_paying_type` choice
- Invariant to test: database state must not say a send path is finalized / active when the raw transaction path says otherwise
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
