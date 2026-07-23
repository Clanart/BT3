# Q958: Misbind trusted context inside withdraw

## Question
Can an unprivileged attacker reach `withdraw` through public gRPC `ClementineAggregator.Withdraw` request and make attacker-controlled the selected operator x-only public-key list bind to the wrong trusted context, so the withdrawal-to-output binding is interpreted for one bridge action while authorizing another, violating the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/aggregator.rs::withdraw
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the selected operator x-only public-key list
- Exploit idea: bind attacker-controlled the selected operator x-only public-key list to the wrong trusted bridge context
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
