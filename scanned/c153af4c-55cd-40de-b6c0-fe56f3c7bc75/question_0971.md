# Q971: Misbind trusted context inside internal_finalized_payout

## Question
Can an unprivileged attacker reach `internal_finalized_payout` through public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw` and make attacker-controlled the `withdrawal_id` bind to the wrong trusted context, so the mapping between a withdrawal and the deposit it spends against is interpreted for one bridge action while authorizing another, violating the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/operator.rs::internal_finalized_payout
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the `withdrawal_id`
- Exploit idea: bind attacker-controlled the `withdrawal_id` to the wrong trusted bridge context
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
