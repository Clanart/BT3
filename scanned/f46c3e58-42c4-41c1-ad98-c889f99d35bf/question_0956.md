# Q956: Misbind aggregate nonce handling in optimistic_payout

## Question
Can an unprivileged attacker make `optimistic_payout` consume attacker-influenced the optimistic withdrawal tuple (`withdrawal_id`, input, output script, amount) under the wrong aggregate-nonce or partial-signature context, corrupting the binding between the optimistic withdrawal tuple and the final signed transaction and breaking the invariant that an optimistic payout signature must be domain-separated from every non-optimistic withdrawal path, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/aggregator.rs::optimistic_payout
- Entrypoint: public gRPC `ClementineAggregator.OptimisticPayout` request
- Attacker controls: the optimistic withdrawal tuple (`withdrawal_id`, input, output script, amount)
- Exploit idea: consume the wrong aggregate-nonce or partial-signature context for the optimistic withdrawal tuple (`withdrawal_id`, input, output script, amount)
- Invariant to test: an optimistic payout signature must be domain-separated from every non-optimistic withdrawal path
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that replays or cross-binds optimistic payout signatures/nonces and assert the verifier/operator reject every mismatched tuple
