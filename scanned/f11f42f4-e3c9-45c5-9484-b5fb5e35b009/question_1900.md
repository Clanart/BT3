# Q1900: Race optimistic_payout across concurrent state

## Question
Can an unprivileged attacker batch, retry, or reorder public gRPC `ClementineAggregator.OptimisticPayout` request interactions around the nonce-session identifier and aggregate nonce used for partial signing so `optimistic_payout` observes inconsistent state across memory, database, and on-chain checks, breaking the invariant that optimistic payout retries must not replay stale aggregate-nonce or partial-signature context, and leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/aggregator.rs::optimistic_payout
- Entrypoint: public gRPC `ClementineAggregator.OptimisticPayout` request
- Attacker controls: the nonce-session identifier and aggregate nonce used for partial signing
- Exploit idea: use retries, batching, or timing around the nonce-session identifier and aggregate nonce used for partial signing to desynchronize state
- Invariant to test: optimistic payout retries must not replay stale aggregate-nonce or partial-signature context
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust test that replays or cross-binds optimistic payout signatures/nonces and assert the verifier/operator reject every mismatched tuple
