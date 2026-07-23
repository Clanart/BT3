# Q2439: Cross-wire presigning material in check_kickoffs_on_chain_and_track

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline to make `check_kickoffs_on_chain_and_track` mix nonce, signature, or key material across two otherwise valid sessions via attacker-controlled the `recovery_taproot_address` in `BaseDeposit`, so the verifier nonce session that a final signature is supposed to consume is authorized under the wrong context and the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle breaks, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/verifier.rs::check_kickoffs_on_chain_and_track
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: mix nonces, partial signatures, or saved signatures across otherwise valid sessions
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
