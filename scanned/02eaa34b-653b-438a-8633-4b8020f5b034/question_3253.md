# Q3253: Decouple emergency protection in on_kickoff_started_entry

## Question
Can an unprivileged attacker push attacker-controlled the `recovery_taproot_address` in `BaseDeposit` through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `on_kickoff_started_entry` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the deposit-to-move-tx binding and breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/states/kickoff.rs::on_kickoff_started_entry
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
