# Q1002: Race generate_challenge_ack_preimages_and_hashes across concurrent state

## Question
Can an unprivileged attacker batch, retry, or reorder public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline interactions around the streamed nonce-session identifiers and public nonce ordering so `generate_challenge_ack_preimages_and_hashes` observes inconsistent state across memory, database, and on-chain checks, breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, and leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/operator.rs::generate_challenge_ack_preimages_and_hashes
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: use retries, batching, or timing around the streamed nonce-session identifiers and public nonce ordering to desynchronize state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
