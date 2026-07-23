# Q3788: Misbind aggregate nonce handling in optimistic_payout

## Question
Can an unprivileged attacker make `optimistic_payout` consume attacker-influenced the nonce-session identifier and aggregate nonce used for partial signing under the wrong aggregate-nonce or partial-signature context, corrupting the binding between the optimistic withdrawal tuple and the final signed transaction and breaking the invariant that an optimistic payout signature must be domain-separated from every non-optimistic withdrawal path, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/aggregator.rs::optimistic_payout
- Entrypoint: public gRPC `ClementineAggregator.OptimisticPayout` request
- Attacker controls: the nonce-session identifier and aggregate nonce used for partial signing
- Exploit idea: consume the wrong aggregate-nonce or partial-signature context for the nonce-session identifier and aggregate nonce used for partial signing
- Invariant to test: an optimistic payout signature must be domain-separated from every non-optimistic withdrawal path
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that replays or cross-binds optimistic payout signatures/nonces and assert the verifier/operator reject every mismatched tuple
