# Q76: Replay context into update_finalized_payouts

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.Withdraw` request that later reaches verifier tracking and challenge logic with attacker-controlled the requested `output_script_pubkey` so `update_finalized_payouts` reuses a previously accepted context, causing the collateral or bridge-controlled UTXO chosen for settlement to be consumed twice and breaking the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/verifier.rs::update_finalized_payouts
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request that later reaches verifier tracking and challenge logic
- Attacker controls: the requested `output_script_pubkey`
- Exploit idea: reuse or replay previously consumed the requested `output_script_pubkey` in a fresh context
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
