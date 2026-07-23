# Q594: Misbind trusted context inside store_for_next_kickoff

## Question
Can an unprivileged attacker reach `store_for_next_kickoff` through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline and make attacker-controlled the set of verifier, operator, or watchtower keys that get associated with the deposit context bind to the wrong trusted context, so the reimbursement path that must remain slashable and recoverable is interpreted for one bridge action while authorizing another, violating the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/builder/transaction/creator.rs::store_for_next_kickoff
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the set of verifier, operator, or watchtower keys that get associated with the deposit context
- Exploit idea: bind attacker-controlled the set of verifier, operator, or watchtower keys that get associated with the deposit context to the wrong trusted bridge context
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
