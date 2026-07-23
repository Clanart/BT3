# Q1886: Leave reusable partial state in handle_new_block

## Question
Can an unprivileged attacker force a partial failure around the aggregate nonce / partial-signature sequencing across repeated requests and then resume public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `handle_new_block` continues from stale intermediate state, causing the nofn aggregate key and covenant context to diverge from the canonical bridge context and breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/task/lcp_syncer.rs::handle_new_block
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the aggregate nonce / partial-signature sequencing across repeated requests
- Exploit idea: force a partial failure around the aggregate nonce / partial-signature sequencing across repeated requests and then resume under changed state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
