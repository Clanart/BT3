# Q3245: Decouple emergency protection in check_if_kickoff_malicious

## Question
Can an unprivileged attacker push attacker-controlled the deposit transaction timing, block placement, and confirmation ordering through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `check_if_kickoff_malicious` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the verifier nonce session that a final signature is supposed to consume and breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/states/event.rs::check_if_kickoff_malicious
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the deposit transaction timing, block placement, and confirmation ordering
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
