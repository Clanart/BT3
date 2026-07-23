# Q3157: Parse a malformed object path in list_rbf_txids_for_ids

## Question
Can an unprivileged attacker craft the `activate_outpoints` / `activate_txids` dependency lists so `list_rbf_txids_for_ids` parses, hashes, or stores one object while later logic signs, verifies, or settles a meaningfully different one, corrupting the binding between raw transaction bytes and the queued tx-sender metadata and violating the invariant that database state must not say a send path is finalized / active when the raw transaction path says otherwise, leading to Critical. Direct loss of funds?

## Target
- File/function: crates/clementine-tx-sender/src/db/tx_sender.rs::list_rbf_txids_for_ids
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `activate_outpoints` / `activate_txids` dependency lists
- Exploit idea: parse, hash, or store one object while later logic settles another by crafting the `activate_outpoints` / `activate_txids` dependency lists
- Invariant to test: database state must not say a send path is finalized / active when the raw transaction path says otherwise
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
