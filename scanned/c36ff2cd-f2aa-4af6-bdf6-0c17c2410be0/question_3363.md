# Q3363: Bypass settlement gating in transfer_outpoints_to_wallet

## Question
Can an unprivileged attacker craft the requested `output_amount` so `transfer_outpoints_to_wallet` satisfies its local gating checks for the wrong bridge action, corrupting the withdrawal-to-output binding and violating the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/operator.rs::transfer_outpoints_to_wallet
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the requested `output_amount`
- Exploit idea: make local checks pass for the wrong bridge action via the requested `output_amount`
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
