# Q3708: Bypass settlement gating in get_payout_info_from_move_txid

## Question
Can an unprivileged attacker craft the requested `output_amount` so `get_payout_info_from_move_txid` satisfies its local gating checks for the wrong bridge action, corrupting the mapping between a withdrawal and the deposit it spends against and violating the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/database/verifier.rs::get_payout_info_from_move_txid
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the requested `output_amount`
- Exploit idea: make local checks pass for the wrong bridge action via the requested `output_amount`
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
