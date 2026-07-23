# Q1282: Exploit replacement logic in verify_new_inputs

## Question
Can an unprivileged attacker shape the `fee_paying_type` choice so `verify_new_inputs` constructs a replacement path that copies the wrong witnesses, inputs, or fee logic, corrupting the finalized/seen-at-height status recorded for the send request and violating the invariant that database state must not say a send path is finalized / active when the raw transaction path says otherwise, leading to Critical. Direct loss of funds?

## Target
- File/function: crates/clementine-tx-sender/src/rbf.rs::verify_new_inputs
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `fee_paying_type` choice
- Exploit idea: copy the wrong witnesses, inputs, or fee logic by shaping the `fee_paying_type` choice
- Invariant to test: database state must not say a send path is finalized / active when the raw transaction path says otherwise
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
