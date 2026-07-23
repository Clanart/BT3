# Q1743: Exploit CPFP fee-path selection in list_unfinalized_fee_payer_utxos

## Question
Can an unprivileged attacker use public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction with crafted the `signed_tx_hex` payload so `list_unfinalized_fee_payer_utxos` attaches CPFP state to the wrong parent, fee payer, or anchor, corrupting the finalized/seen-at-height status recorded for the send request and breaking the invariant that database state must not say a send path is finalized / active when the raw transaction path says otherwise, leading to Critical. Direct loss of funds?

## Target
- File/function: crates/clementine-tx-sender/src/db/tx_sender.rs::list_unfinalized_fee_payer_utxos
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `signed_tx_hex` payload
- Exploit idea: attach CPFP state to the wrong parent, anchor, or fee payer using the `signed_tx_hex` payload
- Invariant to test: database state must not say a send path is finalized / active when the raw transaction path says otherwise
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
