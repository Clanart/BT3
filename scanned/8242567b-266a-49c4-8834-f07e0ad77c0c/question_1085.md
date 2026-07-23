# Q1085: Race nonce_pair across concurrent state

## Question
Can an unprivileged attacker batch, retry, or reorder public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline interactions around the streamed nonce-session identifiers and public nonce ordering so `nonce_pair` observes inconsistent state across memory, database, and on-chain checks, breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, and leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/musig2.rs::nonce_pair
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: use retries, batching, or timing around the streamed nonce-session identifiers and public nonce ordering to desynchronize state
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
