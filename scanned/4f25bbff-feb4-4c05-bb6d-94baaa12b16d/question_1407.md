# Q1407: Race on_initial_collateral_entry across concurrent state

## Question
Can an unprivileged attacker batch, retry, or reorder public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline interactions around the `recovery_taproot_address` in `BaseDeposit` so `on_initial_collateral_entry` observes inconsistent state across memory, database, and on-chain checks, breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, and leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/states/round.rs::on_initial_collateral_entry
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: use retries, batching, or timing around the `recovery_taproot_address` in `BaseDeposit` to desynchronize state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
