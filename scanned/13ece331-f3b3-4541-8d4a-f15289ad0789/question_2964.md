# Q2964: Accept stale finalization in create_payout_txhandler

## Question
Can an unprivileged attacker replay or delay the optional `verification_signature` wrapper through public gRPC `ClementineAggregator.Withdraw` request so `create_payout_txhandler` acts on stale finalization state after the canonical context already changed, corrupting the collateral or bridge-controlled UTXO chosen for settlement and breaking the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/builder/transaction/operator_reimburse.rs::create_payout_txhandler
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the optional `verification_signature` wrapper
- Exploit idea: reuse old the optional `verification_signature` wrapper after a newer canonical context already exists
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
