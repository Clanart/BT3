# Q3237: Accept stale finalization in mark_payout_handled

## Question
Can an unprivileged attacker replay or delay the claimed `input_outpoint` through public gRPC `ClementineAggregator.Withdraw` request so `mark_payout_handled` acts on stale finalization state after the canonical context already changed, corrupting the withdrawal-to-output binding and breaking the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/database/verifier.rs::mark_payout_handled
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the claimed `input_outpoint`
- Exploit idea: reuse old the claimed `input_outpoint` after a newer canonical context already exists
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
