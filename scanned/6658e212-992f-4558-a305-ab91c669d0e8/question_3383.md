# Q3383: Break reimbursement recoverability in check_kickoffs_on_chain_and_track

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with crafted the streamed nonce-session identifiers and public nonce ordering so `check_kickoffs_on_chain_and_track` moves the protocol past the point where reimbursement should remain recoverable, leaving the emergency-stop transaction that should protect the same deposit inconsistent with the assumption that partial pipeline failures must not leave reusable or cross-bindable signing state behind, and leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/verifier.rs::check_kickoffs_on_chain_and_track
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: move bridge state forward while reimbursement/slashability stays tied to older state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
