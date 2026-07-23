# Q2291: Dead-end settlement in update_payout_txs_and_payer_operator_xonly_pk

## Question
Can an unprivileged attacker shape the requested `output_script_pubkey` through public gRPC `ClementineAggregator.Withdraw` request so `update_payout_txs_and_payer_operator_xonly_pk` consumes the valid state transition but leaves no live completion or reimbursement path, corrupting the collateral or bridge-controlled UTXO chosen for settlement and breaking the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/database/verifier.rs::update_payout_txs_and_payer_operator_xonly_pk
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the requested `output_script_pubkey`
- Exploit idea: consume a valid transition while leaving no live completion or reimbursement path
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
