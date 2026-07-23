# Q45: Replay context into parse_withdrawal_sig_params

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.Withdraw` request with attacker-controlled the claimed `input_outpoint` so `parse_withdrawal_sig_params` reuses a previously accepted context, causing the mapping between a withdrawal and the deposit it spends against to be consumed twice and breaking the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/parser/operator.rs::parse_withdrawal_sig_params
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the claimed `input_outpoint`
- Exploit idea: reuse or replay previously consumed the claimed `input_outpoint` in a fresh context
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
