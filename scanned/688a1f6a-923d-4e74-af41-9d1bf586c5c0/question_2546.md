# Q2546: Cross-wire presigning material in create_assert_timeout_txhandlers

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline to make `create_assert_timeout_txhandlers` mix nonce, signature, or key material across two otherwise valid sessions via attacker-controlled the set of verifier, operator, or watchtower keys that get associated with the deposit context, so the nofn aggregate key and covenant context is authorized under the wrong context and the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context breaks, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/builder/transaction/operator_collateral.rs::create_assert_timeout_txhandlers
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the set of verifier, operator, or watchtower keys that get associated with the deposit context
- Exploit idea: mix nonces, partial signatures, or saved signatures across otherwise valid sessions
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
