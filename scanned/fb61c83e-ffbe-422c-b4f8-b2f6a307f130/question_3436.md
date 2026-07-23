# Q3436: Bypass settlement gating in create_payout_txhandler

## Question
Can an unprivileged attacker craft the selected operator x-only public-key list so `create_payout_txhandler` satisfies its local gating checks for the wrong bridge action, corrupting the withdrawal-to-output binding and violating the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/builder/transaction/operator_reimburse.rs::create_payout_txhandler
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the selected operator x-only public-key list
- Exploit idea: make local checks pass for the wrong bridge action via the selected operator x-only public-key list
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
