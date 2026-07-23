# Q1796: Exploit CPFP fee-path selection in copy_witnesses

## Question
Can an unprivileged attacker use public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction with crafted the `signed_tx_hex` payload so `copy_witnesses` attaches CPFP state to the wrong parent, fee payer, or anchor, corrupting the fee-payer UTXO chain selected for CPFP/RBF and breaking the invariant that RBF/CPFP logic must never add, reorder, or reuse UTXOs in a way that changes who pays or what gets spent, leading to Medium. Griefing that causes large-scale disruption of deposits/withdrawals without theft other than by the Aggregator?

## Target
- File/function: crates/clementine-tx-sender/src/rbf.rs::copy_witnesses
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `signed_tx_hex` payload
- Exploit idea: attach CPFP state to the wrong parent, anchor, or fee payer using the `signed_tx_hex` payload
- Invariant to test: RBF/CPFP logic must never add, reorder, or reuse UTXOs in a way that changes who pays or what gets spent
- Expected Immunefi impact: Medium. Griefing that causes large-scale disruption of deposits/withdrawals without theft other than by the Aggregator
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
