# Q3380: Bypass settlement gating in update_finalized_payouts

## Question
Can an unprivileged attacker craft the claimed `input_outpoint` so `update_finalized_payouts` satisfies its local gating checks for the wrong bridge action, corrupting the payout destination or payout amount and violating the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/verifier.rs::update_finalized_payouts
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request that later reaches verifier tracking and challenge logic
- Attacker controls: the claimed `input_outpoint`
- Exploit idea: make local checks pass for the wrong bridge action via the claimed `input_outpoint`
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
