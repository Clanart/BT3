# Q650: Misbind trusted context inside op_return_txout

## Question
Can an unprivileged attacker reach `op_return_txout` through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline and make attacker-controlled the `recovery_taproot_address` in `BaseDeposit` bind to the wrong trusted context, so the emergency-stop transaction that should protect the same deposit is interpreted for one bridge action while authorizing another, violating the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/builder/transaction/mod.rs::op_return_txout
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: bind attacker-controlled the `recovery_taproot_address` in `BaseDeposit` to the wrong trusted bridge context
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
