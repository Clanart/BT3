# Q3333: Bypass settlement gating in withdraw

## Question
Can an unprivileged attacker craft the `withdrawal_id` so `withdraw` satisfies its local gating checks for the wrong bridge action, corrupting the withdrawal-to-output binding and violating the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/operator.rs::withdraw
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the `withdrawal_id`
- Exploit idea: make local checks pass for the wrong bridge action via the `withdrawal_id`
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
