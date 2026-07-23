# Q402: Replay context into update_withdrawal_utxo_from_citrea_withdrawal

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.Withdraw` request with attacker-controlled the requested `output_amount` so `update_withdrawal_utxo_from_citrea_withdrawal` reuses a previously accepted context, causing the withdrawal-to-output binding to be consumed twice and breaking the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/database/verifier.rs::update_withdrawal_utxo_from_citrea_withdrawal
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the requested `output_amount`
- Exploit idea: reuse or replay previously consumed the requested `output_amount` in a fresh context
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
