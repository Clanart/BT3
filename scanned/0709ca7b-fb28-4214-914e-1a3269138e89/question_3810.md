# Q3810: Misbind aggregate nonce handling in optimistic_payout_sign

## Question
Can an unprivileged attacker make `optimistic_payout_sign` consume attacker-influenced the ECDSA verification signature supplied with the optimistic payout request under the wrong aggregate-nonce or partial-signature context, corrupting the optimistic payout transaction that gets partially or fully authorized and breaking the invariant that partial signatures must stay bound to the exact optimistic payout tuple that was reviewed, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/verifier.rs::optimistic_payout_sign
- Entrypoint: public `ClementineAggregator.OptimisticPayout` flow that reaches verifier optimistic payout signing
- Attacker controls: the ECDSA verification signature supplied with the optimistic payout request
- Exploit idea: consume the wrong aggregate-nonce or partial-signature context for the ECDSA verification signature supplied with the optimistic payout request
- Invariant to test: partial signatures must stay bound to the exact optimistic payout tuple that was reviewed
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that replays or cross-binds optimistic payout signatures/nonces and assert the verifier/operator reject every mismatched tuple
