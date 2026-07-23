# Q3454: Break reimbursement recoverability in get_assert_scripts

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with crafted the `recovery_taproot_address` in `BaseDeposit` so `get_assert_scripts` moves the protocol past the point where reimbursement should remain recoverable, leaving the nofn aggregate key and covenant context inconsistent with the assumption that partial pipeline failures must not leave reusable or cross-bindable signing state behind, and leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/bitvm_client.rs::get_assert_scripts
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: move bridge state forward while reimbursement/slashability stays tied to older state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
