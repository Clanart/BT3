# Q3706: Bypass settlement gating in update_withdrawal_utxo_from_citrea_withdrawal

## Question
Can an unprivileged attacker craft the requested `output_script_pubkey` so `update_withdrawal_utxo_from_citrea_withdrawal` satisfies its local gating checks for the wrong bridge action, corrupting the mapping between a withdrawal and the deposit it spends against and violating the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/database/verifier.rs::update_withdrawal_utxo_from_citrea_withdrawal
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the requested `output_script_pubkey`
- Exploit idea: make local checks pass for the wrong bridge action via the requested `output_script_pubkey`
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
