# Q2364: Cross-wire presigning material in aggregator_deposit_nonce_stream_creation_verifier_timeout

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline to make `aggregator_deposit_nonce_stream_creation_verifier_timeout` mix nonce, signature, or key material across two otherwise valid sessions via attacker-controlled the `old_move_txid` in `ReplacementDeposit`, so the verifier nonce session that a final signature is supposed to consume is authorized under the wrong context and the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind breaks, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/aggregator.rs::aggregator_deposit_nonce_stream_creation_verifier_timeout
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `old_move_txid` in `ReplacementDeposit`
- Exploit idea: mix nonces, partial signatures, or saved signatures across otherwise valid sessions
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
