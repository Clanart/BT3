# Q1349: Misbind trusted context inside mark_payout_handled

## Question
Can an unprivileged attacker reach `mark_payout_handled` through public gRPC `ClementineAggregator.Withdraw` request and make attacker-controlled the selected operator x-only public-key list bind to the wrong trusted context, so the payout destination or payout amount is interpreted for one bridge action while authorizing another, violating the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/database/verifier.rs::mark_payout_handled
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the selected operator x-only public-key list
- Exploit idea: bind attacker-controlled the selected operator x-only public-key list to the wrong trusted bridge context
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
