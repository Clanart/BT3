# Q14: Replay context into withdraw

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.Withdraw` request with attacker-controlled the requested `output_amount` so `withdraw` reuses a previously accepted context, causing the operator selection or reimbursement state for the withdrawal to be consumed twice and breaking the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/aggregator.rs::withdraw
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the requested `output_amount`
- Exploit idea: reuse or replay previously consumed the requested `output_amount` in a fresh context
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
