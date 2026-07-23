# Q2371: Cross-wire presigning material in send_emergency_stop_tx

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline to make `send_emergency_stop_tx` mix nonce, signature, or key material across two otherwise valid sessions via attacker-controlled the streamed nonce-session identifiers and public nonce ordering, so the operator signature set attached to a deposit is authorized under the wrong context and the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind breaks, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/aggregator.rs::send_emergency_stop_tx
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: mix nonces, partial signatures, or saved signatures across otherwise valid sessions
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
