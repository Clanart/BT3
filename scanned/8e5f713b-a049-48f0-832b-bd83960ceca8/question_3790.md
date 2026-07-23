# Q3790: Replay context into withdraw

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.Withdraw` request with attacker-controlled the requested `output_amount` so `withdraw` reuses a previously accepted context, causing the payout destination or payout amount to be consumed twice and breaking the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/aggregator.rs::withdraw
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the requested `output_amount`
- Exploit idea: reuse or replay previously consumed the requested `output_amount` in a fresh context
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
