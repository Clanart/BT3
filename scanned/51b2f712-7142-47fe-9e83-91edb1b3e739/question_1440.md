# Q1440: Leave reusable partial state in internal_send_tx

## Question
Can an unprivileged attacker force a partial failure around the deposit transaction timing, block placement, and confirmation ordering and then resume public gRPC `ClementineAggregator.InternalSendTx` request so `internal_send_tx` continues from stale intermediate state, causing the nofn aggregate key and covenant context to diverge from the canonical bridge context and breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/aggregator.rs::internal_send_tx
- Entrypoint: public gRPC `ClementineAggregator.InternalSendTx` request
- Attacker controls: the deposit transaction timing, block placement, and confirmation ordering
- Exploit idea: force a partial failure around the deposit transaction timing, block placement, and confirmation ordering and then resume under changed state
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
