# Q2394: Accept stale finalization in optimistic_payout_sign

## Question
Can an unprivileged attacker replay or delay the nonce-session identifier and aggregate nonce used for partial signing through public `ClementineAggregator.OptimisticPayout` flow that reaches verifier optimistic payout signing so `optimistic_payout_sign` acts on stale finalization state after the canonical context already changed, corrupting the optimistic payout transaction that gets partially or fully authorized and breaking the invariant that partial signatures must stay bound to the exact optimistic payout tuple that was reviewed, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/verifier.rs::optimistic_payout_sign
- Entrypoint: public `ClementineAggregator.OptimisticPayout` flow that reaches verifier optimistic payout signing
- Attacker controls: the nonce-session identifier and aggregate nonce used for partial signing
- Exploit idea: reuse old the nonce-session identifier and aggregate nonce used for partial signing after a newer canonical context already exists
- Invariant to test: partial signatures must stay bound to the exact optimistic payout tuple that was reviewed
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that replays or cross-binds optimistic payout signatures/nonces and assert the verifier/operator reject every mismatched tuple
