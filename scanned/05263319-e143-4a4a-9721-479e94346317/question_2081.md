# Q2081: Confuse replacement linkage in calculate_script_spend_sighash_indexed

## Question
Can an unprivileged attacker shape the streamed nonce-session identifiers and public nonce ordering so `calculate_script_spend_sighash_indexed` confuses replacement and non-replacement contexts, causing the deposit-to-move-tx binding to inherit the wrong history and violating the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/builder/transaction/txhandler.rs::calculate_script_spend_sighash_indexed
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
