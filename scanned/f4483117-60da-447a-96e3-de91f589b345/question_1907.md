# Q1907: Confuse replacement linkage in aggregator_deposit_key_distribution_verifier_timeout

## Question
Can an unprivileged attacker shape the streamed nonce-session identifiers and public nonce ordering so `aggregator_deposit_key_distribution_verifier_timeout` confuses replacement and non-replacement contexts, causing the verifier nonce session that a final signature is supposed to consume to inherit the wrong history and violating the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/aggregator.rs::aggregator_deposit_key_distribution_verifier_timeout
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
