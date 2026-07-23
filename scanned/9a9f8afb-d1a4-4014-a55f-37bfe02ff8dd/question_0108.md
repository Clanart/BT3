# Q108: Replay context into generate_replacement_deposit_address

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with attacker-controlled the deposit transaction timing, block placement, and confirmation ordering so `generate_replacement_deposit_address` reuses a previously accepted context, causing the deposit-to-move-tx binding to be consumed twice and breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/builder/address.rs::generate_replacement_deposit_address
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the deposit transaction timing, block placement, and confirmation ordering
- Exploit idea: reuse or replay previously consumed the deposit transaction timing, block placement, and confirmation ordering in a fresh context
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
