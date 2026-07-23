# Q2790: Cross-wire presigning material in update_non_canonical_block_hashes

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline to make `update_non_canonical_block_hashes` mix nonce, signature, or key material across two otherwise valid sessions via attacker-controlled the streamed nonce-session identifiers and public nonce ordering, so the verifier nonce session that a final signature is supposed to consume is authorized under the wrong context and the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle breaks, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/database/bitcoin_syncer.rs::update_non_canonical_block_hashes
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: mix nonces, partial signatures, or saved signatures across otherwise valid sessions
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
