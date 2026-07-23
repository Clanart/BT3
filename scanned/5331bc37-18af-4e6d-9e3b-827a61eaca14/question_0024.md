# Q24: Replay context into internal_send_tx

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.InternalSendTx` request with attacker-controlled the aggregate nonce / partial-signature sequencing across repeated requests so `internal_send_tx` reuses a previously accepted context, causing the emergency-stop transaction that should protect the same deposit to be consumed twice and breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/rpc/aggregator.rs::internal_send_tx
- Entrypoint: public gRPC `ClementineAggregator.InternalSendTx` request
- Attacker controls: the aggregate nonce / partial-signature sequencing across repeated requests
- Exploit idea: reuse or replay previously consumed the aggregate nonce / partial-signature sequencing across repeated requests in a fresh context
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
