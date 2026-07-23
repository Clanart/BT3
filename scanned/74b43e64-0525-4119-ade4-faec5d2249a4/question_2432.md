# Q2432: Accept stale finalization in sign_optimistic_payout

## Question
Can an unprivileged attacker replay or delay the order and replay timing of optimistic payout signing requests through public `ClementineAggregator.OptimisticPayout` flow that reaches verifier optimistic payout signing so `sign_optimistic_payout` acts on stale finalization state after the canonical context already changed, corrupting the optimistic payout transaction that gets partially or fully authorized and breaking the invariant that optimistic payout retries must not replay stale aggregate-nonce or partial-signature context, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/verifier.rs::sign_optimistic_payout
- Entrypoint: public `ClementineAggregator.OptimisticPayout` flow that reaches verifier optimistic payout signing
- Attacker controls: the order and replay timing of optimistic payout signing requests
- Exploit idea: reuse old the order and replay timing of optimistic payout signing requests after a newer canonical context already exists
- Invariant to test: optimistic payout retries must not replay stale aggregate-nonce or partial-signature context
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust test that replays or cross-binds optimistic payout signatures/nonces and assert the verifier/operator reject every mismatched tuple
