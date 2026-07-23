# Q3338: Break signature/domain separation in optimistic_payout_sign

## Question
Can an unprivileged attacker use public `ClementineAggregator.OptimisticPayout` flow that reaches verifier optimistic payout signing with crafted the optimistic withdrawal tuple (`withdrawal_id`, input, output script, amount) to defeat the message-boundary assumptions inside `optimistic_payout_sign`, so an authorization that should only apply to one context also applies to another, corrupting the partial-signature context attached to a payout request and violating the invariant that an optimistic payout signature must be domain-separated from every non-optimistic withdrawal path, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/verifier.rs::optimistic_payout_sign
- Entrypoint: public `ClementineAggregator.OptimisticPayout` flow that reaches verifier optimistic payout signing
- Attacker controls: the optimistic withdrawal tuple (`withdrawal_id`, input, output script, amount)
- Exploit idea: defeat message-boundary assumptions around the optimistic withdrawal tuple (`withdrawal_id`, input, output script, amount)
- Invariant to test: an optimistic payout signature must be domain-separated from every non-optimistic withdrawal path
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust test that replays or cross-binds optimistic payout signatures/nonces and assert the verifier/operator reject every mismatched tuple
