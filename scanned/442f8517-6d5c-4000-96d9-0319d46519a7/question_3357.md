# Q3357: Bypass settlement gating in handle_finalized_payout

## Question
Can an unprivileged attacker craft the selected operator x-only public-key list so `handle_finalized_payout` satisfies its local gating checks for the wrong bridge action, corrupting the withdrawal-to-output binding and violating the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/operator.rs::handle_finalized_payout
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the selected operator x-only public-key list
- Exploit idea: make local checks pass for the wrong bridge action via the selected operator x-only public-key list
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
