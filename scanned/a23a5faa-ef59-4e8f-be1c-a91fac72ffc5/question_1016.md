# Q1016: Misbind aggregate nonce handling in sign_optimistic_payout

## Question
Can an unprivileged attacker make `sign_optimistic_payout` consume attacker-influenced the optimistic withdrawal tuple (`withdrawal_id`, input, output script, amount) under the wrong aggregate-nonce or partial-signature context, corrupting the optimistic payout transaction that gets partially or fully authorized and breaking the invariant that optimistic payout retries must not replay stale aggregate-nonce or partial-signature context, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/verifier.rs::sign_optimistic_payout
- Entrypoint: public `ClementineAggregator.OptimisticPayout` flow that reaches verifier optimistic payout signing
- Attacker controls: the optimistic withdrawal tuple (`withdrawal_id`, input, output script, amount)
- Exploit idea: consume the wrong aggregate-nonce or partial-signature context for the optimistic withdrawal tuple (`withdrawal_id`, input, output script, amount)
- Invariant to test: optimistic payout retries must not replay stale aggregate-nonce or partial-signature context
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust test that replays or cross-binds optimistic payout signatures/nonces and assert the verifier/operator reject every mismatched tuple
