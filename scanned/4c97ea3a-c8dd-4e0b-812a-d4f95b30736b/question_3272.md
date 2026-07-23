# Q3272: Decouple emergency protection in run_schema_script

## Question
Can an unprivileged attacker push attacker-controlled the `recovery_taproot_address` in `BaseDeposit` through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `run_schema_script` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the reimbursement path that must remain slashable and recoverable and breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/database/mod.rs::run_schema_script
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
