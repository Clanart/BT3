# Q3821: Replay context into parse_withdrawal_sig_params

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.Withdraw` request with attacker-controlled the claimed `input_outpoint` so `parse_withdrawal_sig_params` reuses a previously accepted context, causing the withdrawal-to-output binding to be consumed twice and breaking the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/parser/operator.rs::parse_withdrawal_sig_params
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the claimed `input_outpoint`
- Exploit idea: reuse or replay previously consumed the claimed `input_outpoint` in a fresh context
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
