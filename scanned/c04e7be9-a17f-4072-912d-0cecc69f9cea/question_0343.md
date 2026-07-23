# Q343: Desync metadata inside create_child_tx

## Question
Can an unprivileged attacker submit the `tx_metadata` fields attached to the queued send request through public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction so `create_child_tx` stores or uses metadata that describes a different intent than the raw transaction/proof it later sends, corrupting the finalized/seen-at-height status recorded for the send request and violating the invariant that queued tx metadata, fee paths, and dependency sets must all describe the same transaction intent, leading to Critical. Direct loss of funds?

## Target
- File/function: crates/clementine-tx-sender/src/cpfp.rs::create_child_tx
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `tx_metadata` fields attached to the queued send request
- Exploit idea: make raw bytes and persisted metadata describe different intents using the `tx_metadata` fields attached to the queued send request
- Invariant to test: queued tx metadata, fee paths, and dependency sets must all describe the same transaction intent
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
