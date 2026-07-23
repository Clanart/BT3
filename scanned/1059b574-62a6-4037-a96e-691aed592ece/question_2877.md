# Q2877: Accept stale finalization in parse_withdrawal_sig_params

## Question
Can an unprivileged attacker replay or delay the `withdrawal_id` through public gRPC `ClementineAggregator.Withdraw` request so `parse_withdrawal_sig_params` acts on stale finalization state after the canonical context already changed, corrupting the operator selection or reimbursement state for the withdrawal and breaking the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/parser/operator.rs::parse_withdrawal_sig_params
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the `withdrawal_id`
- Exploit idea: reuse old the `withdrawal_id` after a newer canonical context already exists
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
