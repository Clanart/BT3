# Q2765: Confuse actor or dependency selection in mark_payout_handled

## Question
Can an unprivileged attacker manipulate the user `input_signature` via public gRPC `ClementineAggregator.Withdraw` request so `mark_payout_handled` selects the wrong operator, signer, fee payer, or dependency path, corrupting the collateral or bridge-controlled UTXO chosen for settlement and violating the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/database/verifier.rs::mark_payout_handled
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the user `input_signature`
- Exploit idea: push the wrong operator, signer, fee payer, or dependency path using the user `input_signature`
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
