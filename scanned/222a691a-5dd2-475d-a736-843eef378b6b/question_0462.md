# Q462: Replay context into initial_collateral

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with attacker-controlled the deposit transaction timing, block placement, and confirmation ordering so `initial_collateral` reuses a previously accepted context, causing the deposit-to-move-tx binding to be consumed twice and breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/states/round.rs::initial_collateral
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the deposit transaction timing, block placement, and confirmation ordering
- Exploit idea: reuse or replay previously consumed the deposit transaction timing, block placement, and confirmation ordering in a fresh context
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
