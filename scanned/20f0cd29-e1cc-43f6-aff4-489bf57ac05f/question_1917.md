# Q1917: Dead-end settlement in withdraw

## Question
Can an unprivileged attacker shape the optional `verification_signature` wrapper through public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw` so `withdraw` consumes the valid state transition but leaves no live completion or reimbursement path, corrupting the mapping between a withdrawal and the deposit it spends against and breaking the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/operator.rs::withdraw
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the optional `verification_signature` wrapper
- Exploit idea: consume a valid transition while leaving no live completion or reimbursement path
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
