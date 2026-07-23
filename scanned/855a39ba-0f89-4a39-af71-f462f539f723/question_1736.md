# Q1736: Exploit CPFP fee-path selection in send_cpfp_tx

## Question
Can an unprivileged attacker use public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction with crafted the `cancel_outpoints` / `cancel_txids` dependency lists so `send_cpfp_tx` attaches CPFP state to the wrong parent, fee payer, or anchor, corrupting the finalized/seen-at-height status recorded for the send request and breaking the invariant that database state must not say a send path is finalized / active when the raw transaction path says otherwise, leading to Medium. Griefing that causes large-scale disruption of deposits/withdrawals without theft other than by the Aggregator?

## Target
- File/function: crates/clementine-tx-sender/src/cpfp.rs::send_cpfp_tx
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `cancel_outpoints` / `cancel_txids` dependency lists
- Exploit idea: attach CPFP state to the wrong parent, anchor, or fee payer using the `cancel_outpoints` / `cancel_txids` dependency lists
- Invariant to test: database state must not say a send path is finalized / active when the raw transaction path says otherwise
- Expected Immunefi impact: Medium. Griefing that causes large-scale disruption of deposits/withdrawals without theft other than by the Aggregator
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
