# Q3691: Bypass settlement gating in update_get_payout_txs_from_citrea_withdrawal

## Question
Can an unprivileged attacker craft the selected operator x-only public-key list so `update_get_payout_txs_from_citrea_withdrawal` satisfies its local gating checks for the wrong bridge action, corrupting the payout destination or payout amount and violating the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/database/verifier.rs::update_get_payout_txs_from_citrea_withdrawal
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the selected operator x-only public-key list
- Exploit idea: make local checks pass for the wrong bridge action via the selected operator x-only public-key list
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
