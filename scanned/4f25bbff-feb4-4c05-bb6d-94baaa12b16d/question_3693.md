# Q3693: Break reimbursement recoverability in save_unproven_finalized_block

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with crafted the streamed nonce-session identifiers and public nonce ordering so `save_unproven_finalized_block` moves the protocol past the point where reimbursement should remain recoverable, leaving the deposit-to-move-tx binding inconsistent with the assumption that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, and leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/database/header_chain_prover.rs::save_unproven_finalized_block
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: move bridge state forward while reimbursement/slashability stays tied to older state
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
