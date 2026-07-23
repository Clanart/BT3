# Q3711: Break reimbursement recoverability in signaturesdb_encode_decode_invariant

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with crafted the deposit transaction timing, block placement, and confirmation ordering so `signaturesdb_encode_decode_invariant` moves the protocol past the point where reimbursement should remain recoverable, leaving the nofn aggregate key and covenant context inconsistent with the assumption that partial pipeline failures must not leave reusable or cross-bindable signing state behind, and leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/database/wrapper.rs::signaturesdb_encode_decode_invariant
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the deposit transaction timing, block placement, and confirmation ordering
- Exploit idea: move bridge state forward while reimbursement/slashability stays tied to older state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
