# Q1586: Leave reusable partial state in create_taproot_test_tx

## Question
Can an unprivileged attacker force a partial failure around the streamed nonce-session identifiers and public nonce ordering and then resume public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `create_taproot_test_tx` continues from stale intermediate state, causing the nofn aggregate key and covenant context to diverge from the canonical bridge context and breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/builder/script.rs::create_taproot_test_tx
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: force a partial failure around the streamed nonce-session identifiers and public nonce ordering and then resume under changed state
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
