# Q2436: Confuse actor or dependency selection in update_finalized_payouts

## Question
Can an unprivileged attacker manipulate the `withdrawal_id` via public gRPC `ClementineAggregator.Withdraw` request that later reaches verifier tracking and challenge logic so `update_finalized_payouts` selects the wrong operator, signer, fee payer, or dependency path, corrupting the collateral or bridge-controlled UTXO chosen for settlement and violating the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/verifier.rs::update_finalized_payouts
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request that later reaches verifier tracking and challenge logic
- Attacker controls: the `withdrawal_id`
- Exploit idea: push the wrong operator, signer, fee payer, or dependency path using the `withdrawal_id`
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
