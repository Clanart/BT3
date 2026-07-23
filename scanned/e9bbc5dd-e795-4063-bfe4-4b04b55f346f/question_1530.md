# Q1530: Leave reusable partial state in create_operator_challenge_nack_txhandler

## Question
Can an unprivileged attacker force a partial failure around the set of verifier, operator, or watchtower keys that get associated with the deposit context and then resume public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `create_operator_challenge_nack_txhandler` continues from stale intermediate state, causing the nofn aggregate key and covenant context to diverge from the canonical bridge context and breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/builder/transaction/challenge.rs::create_operator_challenge_nack_txhandler
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the set of verifier, operator, or watchtower keys that get associated with the deposit context
- Exploit idea: force a partial failure around the set of verifier, operator, or watchtower keys that get associated with the deposit context and then resume under changed state
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
