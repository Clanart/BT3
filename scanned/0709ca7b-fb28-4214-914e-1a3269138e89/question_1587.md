# Q1587: Leave reusable partial state in create_nofn_sighash_stream

## Question
Can an unprivileged attacker force a partial failure around the deposit transaction timing, block placement, and confirmation ordering and then resume public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `create_nofn_sighash_stream` continues from stale intermediate state, causing the reimbursement path that must remain slashable and recoverable to diverge from the canonical bridge context and breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/builder/sighash.rs::create_nofn_sighash_stream
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the deposit transaction timing, block placement, and confirmation ordering
- Exploit idea: force a partial failure around the deposit transaction timing, block placement, and confirmation ordering and then resume under changed state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
