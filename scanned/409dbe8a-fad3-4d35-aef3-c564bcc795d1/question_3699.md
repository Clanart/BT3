# Q3699: Break reimbursement recoverability in insert_deposit_data_if_not_exists

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with crafted the aggregate nonce / partial-signature sequencing across repeated requests so `insert_deposit_data_if_not_exists` moves the protocol past the point where reimbursement should remain recoverable, leaving the emergency-stop transaction that should protect the same deposit inconsistent with the assumption that partial pipeline failures must not leave reusable or cross-bindable signing state behind, and leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/database/operator.rs::insert_deposit_data_if_not_exists
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the aggregate nonce / partial-signature sequencing across repeated requests
- Exploit idea: move bridge state forward while reimbursement/slashability stays tied to older state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
