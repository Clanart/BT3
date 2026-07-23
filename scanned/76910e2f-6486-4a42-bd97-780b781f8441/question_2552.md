# Q2552: Cross-wire presigning material in calculate_pubkey_spend_sighash

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline to make `calculate_pubkey_spend_sighash` mix nonce, signature, or key material across two otherwise valid sessions via attacker-controlled the set of verifier, operator, or watchtower keys that get associated with the deposit context, so the reimbursement path that must remain slashable and recoverable is authorized under the wrong context and the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context breaks, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/builder/transaction/txhandler.rs::calculate_pubkey_spend_sighash
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the set of verifier, operator, or watchtower keys that get associated with the deposit context
- Exploit idea: mix nonces, partial signatures, or saved signatures across otherwise valid sessions
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
