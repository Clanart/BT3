# Q3803: Replay context into internal_finalized_payout

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw` with attacker-controlled the selected operator x-only public-key list so `internal_finalized_payout` reuses a previously accepted context, causing the operator selection or reimbursement state for the withdrawal to be consumed twice and breaking the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/operator.rs::internal_finalized_payout
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the selected operator x-only public-key list
- Exploit idea: reuse or replay previously consumed the selected operator x-only public-key list in a fresh context
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
