# Q651: Misbind trusted context inside create_move_to_vault_txhandler

## Question
Can an unprivileged attacker reach `create_move_to_vault_txhandler` through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline and make attacker-controlled the `recovery_taproot_address` in `BaseDeposit` bind to the wrong trusted context, so the nofn aggregate key and covenant context is interpreted for one bridge action while authorizing another, violating the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/builder/transaction/mod.rs::create_move_to_vault_txhandler
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: bind attacker-controlled the `recovery_taproot_address` in `BaseDeposit` to the wrong trusted bridge context
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
