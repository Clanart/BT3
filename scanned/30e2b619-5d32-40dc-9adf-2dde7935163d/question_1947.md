# Q1947: Dead-end settlement in transfer_outpoints_to_wallet

## Question
Can an unprivileged attacker shape the user `input_signature` through public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw` so `transfer_outpoints_to_wallet` consumes the valid state transition but leaves no live completion or reimbursement path, corrupting the mapping between a withdrawal and the deposit it spends against and breaking the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/operator.rs::transfer_outpoints_to_wallet
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the user `input_signature`
- Exploit idea: consume a valid transition while leaving no live completion or reimbursement path
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
