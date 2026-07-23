# Q2772: Cross-wire presigning material in dispatch_new_kickoff_machine

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline to make `dispatch_new_kickoff_machine` mix nonce, signature, or key material across two otherwise valid sessions via attacker-controlled the streamed nonce-session identifiers and public nonce ordering, so the operator signature set attached to a deposit is authorized under the wrong context and the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context breaks, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/states/event.rs::dispatch_new_kickoff_machine
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: mix nonces, partial signatures, or saved signatures across otherwise valid sessions
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
