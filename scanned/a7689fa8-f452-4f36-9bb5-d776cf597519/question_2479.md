# Q2479: Cross-wire presigning material in new_context_for_kickoff

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline to make `new_context_for_kickoff` mix nonce, signature, or key material across two otherwise valid sessions via attacker-controlled the deposit transaction timing, block placement, and confirmation ordering, so the emergency-stop transaction that should protect the same deposit is authorized under the wrong context and the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context breaks, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/builder/transaction/creator.rs::new_context_for_kickoff
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the deposit transaction timing, block placement, and confirmation ordering
- Exploit idea: mix nonces, partial signatures, or saved signatures across otherwise valid sessions
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
