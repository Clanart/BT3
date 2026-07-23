# Q542: Misbind trusted context inside deposit_sign

## Question
Can an unprivileged attacker reach `deposit_sign` through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline and make attacker-controlled the `recovery_taproot_address` in `BaseDeposit` bind to the wrong trusted context, so the nofn aggregate key and covenant context is interpreted for one bridge action while authorizing another, violating the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/verifier.rs::deposit_sign
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: bind attacker-controlled the `recovery_taproot_address` in `BaseDeposit` to the wrong trusted bridge context
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
