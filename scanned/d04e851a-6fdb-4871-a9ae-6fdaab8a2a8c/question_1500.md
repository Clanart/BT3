# Q1500: Leave reusable partial state in send_unspent_kickoff_connectors

## Question
Can an unprivileged attacker force a partial failure around the aggregate nonce / partial-signature sequencing across repeated requests and then resume public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `send_unspent_kickoff_connectors` continues from stale intermediate state, causing the emergency-stop transaction that should protect the same deposit to diverge from the canonical bridge context and breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/verifier.rs::send_unspent_kickoff_connectors
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the aggregate nonce / partial-signature sequencing across repeated requests
- Exploit idea: force a partial failure around the aggregate nonce / partial-signature sequencing across repeated requests and then resume under changed state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
