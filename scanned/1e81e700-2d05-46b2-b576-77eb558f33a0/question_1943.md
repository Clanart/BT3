# Q1943: Dead-end settlement in withdraw

## Question
Can an unprivileged attacker shape the requested `output_amount` through public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw` so `withdraw` consumes the valid state transition but leaves no live completion or reimbursement path, corrupting the mapping between a withdrawal and the deposit it spends against and breaking the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/operator.rs::withdraw
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the requested `output_amount`
- Exploit idea: consume a valid transition while leaving no live completion or reimbursement path
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
