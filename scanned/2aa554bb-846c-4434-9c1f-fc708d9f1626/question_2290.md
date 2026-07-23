# Q2290: Dead-end settlement in update_withdrawal_utxo_from_citrea_withdrawal

## Question
Can an unprivileged attacker shape the `withdrawal_id` through public gRPC `ClementineAggregator.Withdraw` request so `update_withdrawal_utxo_from_citrea_withdrawal` consumes the valid state transition but leaves no live completion or reimbursement path, corrupting the collateral or bridge-controlled UTXO chosen for settlement and breaking the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/database/verifier.rs::update_withdrawal_utxo_from_citrea_withdrawal
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the `withdrawal_id`
- Exploit idea: consume a valid transition while leaving no live completion or reimbursement path
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
