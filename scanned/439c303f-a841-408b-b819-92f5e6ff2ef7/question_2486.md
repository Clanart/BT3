# Q2486: Cross-wire presigning material in create_replacement_deposit_txhandler

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline to make `create_replacement_deposit_txhandler` mix nonce, signature, or key material across two otherwise valid sessions via attacker-controlled the deposit transaction timing, block placement, and confirmation ordering, so the reimbursement path that must remain slashable and recoverable is authorized under the wrong context and the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle breaks, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/builder/transaction/mod.rs::create_replacement_deposit_txhandler
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the deposit transaction timing, block placement, and confirmation ordering
- Exploit idea: mix nonces, partial signatures, or saved signatures across otherwise valid sessions
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
