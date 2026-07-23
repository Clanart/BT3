# Q3379: Bypass settlement gating in update_citrea_deposit_and_withdrawals

## Question
Can an unprivileged attacker craft the requested `output_amount` so `update_citrea_deposit_and_withdrawals` satisfies its local gating checks for the wrong bridge action, corrupting the collateral or bridge-controlled UTXO chosen for settlement and violating the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/verifier.rs::update_citrea_deposit_and_withdrawals
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request that later reaches verifier tracking and challenge logic
- Attacker controls: the requested `output_amount`
- Exploit idea: make local checks pass for the wrong bridge action via the requested `output_amount`
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
