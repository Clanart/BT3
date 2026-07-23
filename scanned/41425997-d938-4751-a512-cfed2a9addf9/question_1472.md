# Q1472: Leave reusable partial state in generate_kickoff_winternitz_pubkeys

## Question
Can an unprivileged attacker force a partial failure around the `recovery_taproot_address` in `BaseDeposit` and then resume public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `generate_kickoff_winternitz_pubkeys` continues from stale intermediate state, causing the verifier nonce session that a final signature is supposed to consume to diverge from the canonical bridge context and breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/operator.rs::generate_kickoff_winternitz_pubkeys
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: force a partial failure around the `recovery_taproot_address` in `BaseDeposit` and then resume under changed state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
