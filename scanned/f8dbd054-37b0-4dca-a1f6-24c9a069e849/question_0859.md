# Q859: Break signature/domain separation in update_get_payout_txs_from_citrea_withdrawal

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.Withdraw` request with crafted the `withdrawal_id` to defeat the message-boundary assumptions inside `update_get_payout_txs_from_citrea_withdrawal`, so an authorization that should only apply to one context also applies to another, corrupting the withdrawal-to-output binding and violating the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/database/verifier.rs::update_get_payout_txs_from_citrea_withdrawal
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the `withdrawal_id`
- Exploit idea: defeat message-boundary assumptions around the `withdrawal_id`
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
