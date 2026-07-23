# Q1782: Exploit CPFP fee-path selection in delete_try_to_send_tx

## Question
Can an unprivileged attacker use public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction with crafted the `activate_outpoints` / `activate_txids` dependency lists so `delete_try_to_send_tx` attaches CPFP state to the wrong parent, fee payer, or anchor, corrupting the binding between raw transaction bytes and the queued tx-sender metadata and breaking the invariant that RBF/CPFP logic must never add, reorder, or reuse UTXOs in a way that changes who pays or what gets spent, leading to Medium. Griefing that causes large-scale disruption of deposits/withdrawals without theft other than by the Aggregator?

## Target
- File/function: crates/clementine-tx-sender/src/db/tx_sender.rs::delete_try_to_send_tx
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `activate_outpoints` / `activate_txids` dependency lists
- Exploit idea: attach CPFP state to the wrong parent, anchor, or fee payer using the `activate_outpoints` / `activate_txids` dependency lists
- Invariant to test: RBF/CPFP logic must never add, reorder, or reuse UTXOs in a way that changes who pays or what gets spent
- Expected Immunefi impact: Medium. Griefing that causes large-scale disruption of deposits/withdrawals without theft other than by the Aggregator
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
