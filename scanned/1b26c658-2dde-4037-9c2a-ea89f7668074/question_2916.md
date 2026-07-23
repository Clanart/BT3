# Q2916: Decouple emergency protection in send_unspent_kickoff_connectors

## Question
Can an unprivileged attacker push attacker-controlled the deposit transaction timing, block placement, and confirmation ordering through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `send_unspent_kickoff_connectors` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the nofn aggregate key and covenant context and breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/verifier.rs::send_unspent_kickoff_connectors
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the deposit transaction timing, block placement, and confirmation ordering
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
