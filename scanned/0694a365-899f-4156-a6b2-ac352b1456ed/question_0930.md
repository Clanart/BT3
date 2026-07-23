# Q930: Misbind trusted context inside save_state_to_db

## Question
Can an unprivileged attacker reach `save_state_to_db` through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline and make attacker-controlled the `recovery_taproot_address` in `BaseDeposit` bind to the wrong trusted context, so the reimbursement path that must remain slashable and recoverable is interpreted for one bridge action while authorizing another, violating the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/states/mod.rs::save_state_to_db
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: bind attacker-controlled the `recovery_taproot_address` in `BaseDeposit` to the wrong trusted bridge context
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
