# Q2274: Confuse replacement linkage in upsert_get_deposit_signatures

## Question
Can an unprivileged attacker shape the set of verifier, operator, or watchtower keys that get associated with the deposit context so `upsert_get_deposit_signatures` confuses replacement and non-replacement contexts, causing the operator signature set attached to a deposit to inherit the wrong history and violating the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/database/operator.rs::upsert_get_deposit_signatures
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the set of verifier, operator, or watchtower keys that get associated with the deposit context
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
