# Q1946: Confuse replacement linkage in generate_challenge_ack_preimages_and_hashes

## Question
Can an unprivileged attacker shape the set of verifier, operator, or watchtower keys that get associated with the deposit context so `generate_challenge_ack_preimages_and_hashes` confuses replacement and non-replacement contexts, causing the reimbursement path that must remain slashable and recoverable to inherit the wrong history and violating the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/operator.rs::generate_challenge_ack_preimages_and_hashes
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the set of verifier, operator, or watchtower keys that get associated with the deposit context
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
