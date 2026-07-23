# Q1933: Dead-end settlement in parse_withdrawal_sig_params

## Question
Can an unprivileged attacker shape the selected operator x-only public-key list through public gRPC `ClementineAggregator.Withdraw` request so `parse_withdrawal_sig_params` consumes the valid state transition but leaves no live completion or reimbursement path, corrupting the payout destination or payout amount and breaking the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/parser/operator.rs::parse_withdrawal_sig_params
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the selected operator x-only public-key list
- Exploit idea: consume a valid transition while leaving no live completion or reimbursement path
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
