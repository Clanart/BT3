# Q2961: Decouple emergency protection in create_burn_unused_kickoff_connectors_txhandler

## Question
Can an unprivileged attacker push attacker-controlled the streamed nonce-session identifiers and public nonce ordering through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `create_burn_unused_kickoff_connectors_txhandler` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the reimbursement path that must remain slashable and recoverable and breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/builder/transaction/operator_collateral.rs::create_burn_unused_kickoff_connectors_txhandler
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
