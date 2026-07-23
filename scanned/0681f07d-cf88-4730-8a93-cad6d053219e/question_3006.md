# Q3006: Decouple emergency protection in calculate_hash_of_sighashes

## Question
Can an unprivileged attacker push attacker-controlled the `deposit_outpoint` and its on-chain prevout details through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `calculate_hash_of_sighashes` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the operator signature set attached to a deposit and breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/builder/sighash.rs::calculate_hash_of_sighashes
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `deposit_outpoint` and its on-chain prevout details
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
