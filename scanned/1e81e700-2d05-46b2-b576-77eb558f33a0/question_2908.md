# Q2908: Accept stale finalization in update_finalized_payouts

## Question
Can an unprivileged attacker replay or delay the user `input_signature` through public gRPC `ClementineAggregator.Withdraw` request that later reaches verifier tracking and challenge logic so `update_finalized_payouts` acts on stale finalization state after the canonical context already changed, corrupting the withdrawal-to-output binding and breaking the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/verifier.rs::update_finalized_payouts
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request that later reaches verifier tracking and challenge logic
- Attacker controls: the user `input_signature`
- Exploit idea: reuse old the user `input_signature` after a newer canonical context already exists
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
