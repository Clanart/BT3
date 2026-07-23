# Q2871: Decouple emergency protection in internal_create_signed_txs

## Question
Can an unprivileged attacker push attacker-controlled the set of verifier, operator, or watchtower keys that get associated with the deposit context through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `internal_create_signed_txs` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the verifier nonce session that a final signature is supposed to consume and breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/verifier.rs::internal_create_signed_txs
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the set of verifier, operator, or watchtower keys that get associated with the deposit context
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
