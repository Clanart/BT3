# Q839: Race update_synced_height across concurrent state

## Question
Can an unprivileged attacker batch, retry, or reorder public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction interactions around the `activate_outpoints` / `activate_txids` dependency lists so `update_synced_height` observes inconsistent state across memory, database, and on-chain checks, breaking the invariant that database state must not say a send path is finalized / active when the raw transaction path says otherwise, and leading to Critical. Direct loss of funds?

## Target
- File/function: crates/clementine-tx-sender/src/db/tx_sender.rs::update_synced_height
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `activate_outpoints` / `activate_txids` dependency lists
- Exploit idea: use retries, batching, or timing around the `activate_outpoints` / `activate_txids` dependency lists to desynchronize state
- Invariant to test: database state must not say a send path is finalized / active when the raw transaction path says otherwise
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
