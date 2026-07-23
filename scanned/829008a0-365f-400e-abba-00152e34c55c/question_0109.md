# Q109: Replay context into complete_with_kickoff_txid

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with attacker-controlled the `recovery_taproot_address` in `BaseDeposit` so `complete_with_kickoff_txid` reuses a previously accepted context, causing the verifier nonce session that a final signature is supposed to consume to be consumed twice and breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/builder/sighash.rs::complete_with_kickoff_txid
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: reuse or replay previously consumed the `recovery_taproot_address` in `BaseDeposit` in a fresh context
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
