# Q3270: Decouple emergency protection in fetch_and_save_missing_blocks

## Question
Can an unprivileged attacker push attacker-controlled the `recovery_taproot_address` in `BaseDeposit` through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `fetch_and_save_missing_blocks` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the reimbursement path that must remain slashable and recoverable and breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/database/header_chain_prover.rs::fetch_and_save_missing_blocks
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
