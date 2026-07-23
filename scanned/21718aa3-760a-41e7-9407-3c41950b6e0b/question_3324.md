# Q3324: Break reimbursement recoverability in aggregator_deposit_key_distribution_operator_timeout

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with crafted the `recovery_taproot_address` in `BaseDeposit` so `aggregator_deposit_key_distribution_operator_timeout` moves the protocol past the point where reimbursement should remain recoverable, leaving the reimbursement path that must remain slashable and recoverable inconsistent with the assumption that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, and leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/aggregator.rs::aggregator_deposit_key_distribution_operator_timeout
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: move bridge state forward while reimbursement/slashability stays tied to older state
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
