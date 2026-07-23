# Q2282: Confuse replacement linkage in insert_operator_challenge_ack_hashes_if_not_exist

## Question
Can an unprivileged attacker shape the `evm_address` in `BaseDeposit` so `insert_operator_challenge_ack_hashes_if_not_exist` confuses replacement and non-replacement contexts, causing the reimbursement path that must remain slashable and recoverable to inherit the wrong history and violating the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/database/operator.rs::insert_operator_challenge_ack_hashes_if_not_exist
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `evm_address` in `BaseDeposit`
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
