# Q1516: Leave reusable partial state in tx_sign_winternitz

## Question
Can an unprivileged attacker force a partial failure around the deposit transaction timing, block placement, and confirmation ordering and then resume public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `tx_sign_winternitz` continues from stale intermediate state, causing the operator signature set attached to a deposit to diverge from the canonical bridge context and breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/actor.rs::tx_sign_winternitz
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the deposit transaction timing, block placement, and confirmation ordering
- Exploit idea: force a partial failure around the deposit transaction timing, block placement, and confirmation ordering and then resume under changed state
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
