# Q179: Replay context into create_move_to_vault_txhandler

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with attacker-controlled the `evm_address` in `BaseDeposit` so `create_move_to_vault_txhandler` reuses a previously accepted context, causing the deposit-to-move-tx binding to be consumed twice and breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/builder/transaction/mod.rs::create_move_to_vault_txhandler
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `evm_address` in `BaseDeposit`
- Exploit idea: reuse or replay previously consumed the `evm_address` in `BaseDeposit` in a fresh context
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
