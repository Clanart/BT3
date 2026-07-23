# Q3709: Bypass settlement gating in mark_payout_handled

## Question
Can an unprivileged attacker craft the requested `output_script_pubkey` so `mark_payout_handled` satisfies its local gating checks for the wrong bridge action, corrupting the payout destination or payout amount and violating the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/database/verifier.rs::mark_payout_handled
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the requested `output_script_pubkey`
- Exploit idea: make local checks pass for the wrong bridge action via the requested `output_script_pubkey`
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
