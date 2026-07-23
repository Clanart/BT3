# Q1436: Leave reusable partial state in aggregator_deposit_key_distribution_operator_timeout

## Question
Can an unprivileged attacker force a partial failure around the set of verifier, operator, or watchtower keys that get associated with the deposit context and then resume public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `aggregator_deposit_key_distribution_operator_timeout` continues from stale intermediate state, causing the nofn aggregate key and covenant context to diverge from the canonical bridge context and breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/aggregator.rs::aggregator_deposit_key_distribution_operator_timeout
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the set of verifier, operator, or watchtower keys that get associated with the deposit context
- Exploit idea: force a partial failure around the set of verifier, operator, or watchtower keys that get associated with the deposit context and then resume under changed state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
