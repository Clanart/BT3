# Q2536: Cross-wire presigning material in create_round_txhandlers

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline to make `create_round_txhandlers` mix nonce, signature, or key material across two otherwise valid sessions via attacker-controlled the aggregate nonce / partial-signature sequencing across repeated requests, so the emergency-stop transaction that should protect the same deposit is authorized under the wrong context and the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind breaks, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/builder/transaction/creator.rs::create_round_txhandlers
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the aggregate nonce / partial-signature sequencing across repeated requests
- Exploit idea: mix nonces, partial signatures, or saved signatures across otherwise valid sessions
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
