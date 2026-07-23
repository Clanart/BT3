# Q837: Race set_activate_txid_mempool_status across concurrent state

## Question
Can an unprivileged attacker batch, retry, or reorder public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction interactions around the ordering of repeated enqueue / replace / cancel requests so `set_activate_txid_mempool_status` observes inconsistent state across memory, database, and on-chain checks, breaking the invariant that RBF/CPFP logic must never add, reorder, or reuse UTXOs in a way that changes who pays or what gets spent, and leading to Medium. Griefing that causes large-scale disruption of deposits/withdrawals without theft other than by the Aggregator?

## Target
- File/function: crates/clementine-tx-sender/src/db/tx_sender.rs::set_activate_txid_mempool_status
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the ordering of repeated enqueue / replace / cancel requests
- Exploit idea: use retries, batching, or timing around the ordering of repeated enqueue / replace / cancel requests to desynchronize state
- Invariant to test: RBF/CPFP logic must never add, reorder, or reuse UTXOs in a way that changes who pays or what gets spent
- Expected Immunefi impact: Medium. Griefing that causes large-scale disruption of deposits/withdrawals without theft other than by the Aggregator
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
