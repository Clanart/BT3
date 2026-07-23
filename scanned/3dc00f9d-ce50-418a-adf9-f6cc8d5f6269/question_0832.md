# Q832: Race update_tx_debug_sending_state across concurrent state

## Question
Can an unprivileged attacker batch, retry, or reorder public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction interactions around the ordering of repeated enqueue / replace / cancel requests so `update_tx_debug_sending_state` observes inconsistent state across memory, database, and on-chain checks, breaking the invariant that database state must not say a send path is finalized / active when the raw transaction path says otherwise, and leading to Critical. Direct loss of funds?

## Target
- File/function: crates/clementine-tx-sender/src/db/tx_sender.rs::update_tx_debug_sending_state
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the ordering of repeated enqueue / replace / cancel requests
- Exploit idea: use retries, batching, or timing around the ordering of repeated enqueue / replace / cancel requests to desynchronize state
- Invariant to test: database state must not say a send path is finalized / active when the raw transaction path says otherwise
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
