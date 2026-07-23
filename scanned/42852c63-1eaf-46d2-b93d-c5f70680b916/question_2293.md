# Q2293: Dead-end settlement in mark_payout_handled

## Question
Can an unprivileged attacker shape the `withdrawal_id` through public gRPC `ClementineAggregator.Withdraw` request so `mark_payout_handled` consumes the valid state transition but leaves no live completion or reimbursement path, corrupting the operator selection or reimbursement state for the withdrawal and breaking the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/database/verifier.rs::mark_payout_handled
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the `withdrawal_id`
- Exploit idea: consume a valid transition while leaving no live completion or reimbursement path
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
