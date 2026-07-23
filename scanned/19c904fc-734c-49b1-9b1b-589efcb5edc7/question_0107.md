# Q107: Replay context into generate_deposit_address

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with attacker-controlled the streamed nonce-session identifiers and public nonce ordering so `generate_deposit_address` reuses a previously accepted context, causing the deposit-to-move-tx binding to be consumed twice and breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/builder/address.rs::generate_deposit_address
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: reuse or replay previously consumed the streamed nonce-session identifiers and public nonce ordering in a fresh context
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
