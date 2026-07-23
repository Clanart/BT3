# Q2860: Accept stale finalization in internal_withdraw

## Question
Can an unprivileged attacker replay or delay the retry / batching / timing of repeated withdrawal requests through public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw` so `internal_withdraw` acts on stale finalization state after the canonical context already changed, corrupting the operator selection or reimbursement state for the withdrawal and breaking the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/operator.rs::internal_withdraw
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the retry / batching / timing of repeated withdrawal requests
- Exploit idea: reuse old the retry / batching / timing of repeated withdrawal requests after a newer canonical context already exists
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
