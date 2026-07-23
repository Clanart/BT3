# Q320: Desync metadata inside send_cpfp_tx

## Question
Can an unprivileged attacker submit the `tx_metadata` fields attached to the queued send request through public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction so `send_cpfp_tx` stores or uses metadata that describes a different intent than the raw transaction/proof it later sends, corrupting the binding between raw transaction bytes and the queued tx-sender metadata and violating the invariant that database state must not say a send path is finalized / active when the raw transaction path says otherwise, leading to Medium. Griefing that causes large-scale disruption of deposits/withdrawals without theft other than by the Aggregator?

## Target
- File/function: crates/clementine-tx-sender/src/cpfp.rs::send_cpfp_tx
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `tx_metadata` fields attached to the queued send request
- Exploit idea: make raw bytes and persisted metadata describe different intents using the `tx_metadata` fields attached to the queued send request
- Invariant to test: database state must not say a send path is finalized / active when the raw transaction path says otherwise
- Expected Immunefi impact: Medium. Griefing that causes large-scale disruption of deposits/withdrawals without theft other than by the Aggregator
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
