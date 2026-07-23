# Q2064: Confuse replacement linkage in create_round_txhandlers

## Question
Can an unprivileged attacker shape the streamed nonce-session identifiers and public nonce ordering so `create_round_txhandlers` confuses replacement and non-replacement contexts, causing the operator signature set attached to a deposit to inherit the wrong history and violating the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/builder/transaction/creator.rs::create_round_txhandlers
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
