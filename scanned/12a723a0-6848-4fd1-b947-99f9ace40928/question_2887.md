# Q2887: Accept stale finalization in withdraw

## Question
Can an unprivileged attacker replay or delay the selected operator x-only public-key list through public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw` so `withdraw` acts on stale finalization state after the canonical context already changed, corrupting the collateral or bridge-controlled UTXO chosen for settlement and breaking the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/operator.rs::withdraw
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the selected operator x-only public-key list
- Exploit idea: reuse old the selected operator x-only public-key list after a newer canonical context already exists
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
