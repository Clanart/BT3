# Q1137: Race calculate_script_spend_sighash_indexed across concurrent state

## Question
Can an unprivileged attacker batch, retry, or reorder public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline interactions around the `recovery_taproot_address` in `BaseDeposit` so `calculate_script_spend_sighash_indexed` observes inconsistent state across memory, database, and on-chain checks, breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, and leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/builder/transaction/txhandler.rs::calculate_script_spend_sighash_indexed
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: use retries, batching, or timing around the `recovery_taproot_address` in `BaseDeposit` to desynchronize state
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
