# Q2837: Decouple emergency protection in nonce_aggregator

## Question
Can an unprivileged attacker push attacker-controlled the streamed nonce-session identifiers and public nonce ordering through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `nonce_aggregator` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the emergency-stop transaction that should protect the same deposit and breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/aggregator.rs::nonce_aggregator
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
