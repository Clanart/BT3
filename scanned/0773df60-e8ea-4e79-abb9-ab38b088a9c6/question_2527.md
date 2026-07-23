# Q2527: Cross-wire presigning material in generate_script_inputs

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline to make `generate_script_inputs` mix nonce, signature, or key material across two otherwise valid sessions via attacker-controlled the `old_move_txid` in `ReplacementDeposit`, so the operator signature set attached to a deposit is authorized under the wrong context and the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle breaks, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/builder/script.rs::generate_script_inputs
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `old_move_txid` in `ReplacementDeposit`
- Exploit idea: mix nonces, partial signatures, or saved signatures across otherwise valid sessions
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
