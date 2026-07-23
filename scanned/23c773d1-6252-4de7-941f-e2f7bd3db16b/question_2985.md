# Q2985: Decouple emergency protection in get_assert_taproot_leaf_hashes

## Question
Can an unprivileged attacker push attacker-controlled the streamed nonce-session identifiers and public nonce ordering through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `get_assert_taproot_leaf_hashes` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the deposit-to-move-tx binding and breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/bitvm_client.rs::get_assert_taproot_leaf_hashes
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
