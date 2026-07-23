# Q2851: Decouple emergency protection in aggregator_deposit_key_distribution_verifier_timeout

## Question
Can an unprivileged attacker push attacker-controlled the set of verifier, operator, or watchtower keys that get associated with the deposit context through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `aggregator_deposit_key_distribution_verifier_timeout` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the emergency-stop transaction that should protect the same deposit and breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/aggregator.rs::aggregator_deposit_key_distribution_verifier_timeout
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the set of verifier, operator, or watchtower keys that get associated with the deposit context
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
