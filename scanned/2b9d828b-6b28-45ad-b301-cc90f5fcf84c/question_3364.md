# Q3364: Bypass settlement gating in get_reimbursement_txs

## Question
Can an unprivileged attacker craft the user `input_signature` so `get_reimbursement_txs` satisfies its local gating checks for the wrong bridge action, corrupting the operator selection or reimbursement state for the withdrawal and violating the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/operator.rs::get_reimbursement_txs
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the user `input_signature`
- Exploit idea: make local checks pass for the wrong bridge action via the user `input_signature`
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
