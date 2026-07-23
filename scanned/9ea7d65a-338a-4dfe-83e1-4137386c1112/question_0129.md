# Q129: Replay context into create_burn_unused_kickoff_connectors_txhandler

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with attacker-controlled the set of verifier, operator, or watchtower keys that get associated with the deposit context so `create_burn_unused_kickoff_connectors_txhandler` reuses a previously accepted context, causing the reimbursement path that must remain slashable and recoverable to be consumed twice and breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/builder/transaction/operator_collateral.rs::create_burn_unused_kickoff_connectors_txhandler
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the set of verifier, operator, or watchtower keys that get associated with the deposit context
- Exploit idea: reuse or replay previously consumed the set of verifier, operator, or watchtower keys that get associated with the deposit context in a fresh context
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
