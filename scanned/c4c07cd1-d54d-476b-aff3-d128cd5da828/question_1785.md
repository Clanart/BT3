# Q1785: Exploit CPFP fee-path selection in empty_tx

## Question
Can an unprivileged attacker use public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction with crafted the `rbf_signing_info` object so `empty_tx` attaches CPFP state to the wrong parent, fee payer, or anchor, corrupting the finalized/seen-at-height status recorded for the send request and breaking the invariant that queued tx metadata, fee paths, and dependency sets must all describe the same transaction intent, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: crates/clementine-tx-sender/src/db/tx_sender.rs::empty_tx
- Entrypoint: public JSON-RPC `send_tx` request or a user-triggered automation path that enqueues a Bitcoin transaction
- Attacker controls: the `rbf_signing_info` object
- Exploit idea: attach CPFP state to the wrong parent, anchor, or fee payer using the `rbf_signing_info` object
- Invariant to test: queued tx metadata, fee paths, and dependency sets must all describe the same transaction intent
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust test that enqueues conflicting raw tx / metadata / RBF / cancel / activate combinations and assert the DB and final spend path remain consistent
