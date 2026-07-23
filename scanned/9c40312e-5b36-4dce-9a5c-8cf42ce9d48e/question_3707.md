# Q3707: Bypass settlement gating in update_payout_txs_and_payer_operator_xonly_pk

## Question
Can an unprivileged attacker craft the selected operator x-only public-key list so `update_payout_txs_and_payer_operator_xonly_pk` satisfies its local gating checks for the wrong bridge action, corrupting the mapping between a withdrawal and the deposit it spends against and violating the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/database/verifier.rs::update_payout_txs_and_payer_operator_xonly_pk
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the selected operator x-only public-key list
- Exploit idea: make local checks pass for the wrong bridge action via the selected operator x-only public-key list
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
