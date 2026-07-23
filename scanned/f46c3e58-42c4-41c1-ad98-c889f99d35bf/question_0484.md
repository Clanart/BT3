# Q484: Break signature/domain separation in optimistic_payout

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.OptimisticPayout` request with crafted the order and replay timing of optimistic payout signing requests to defeat the message-boundary assumptions inside `optimistic_payout`, so an authorization that should only apply to one context also applies to another, corrupting the optimistic payout transaction that gets partially or fully authorized and violating the invariant that optimistic payout retries must not replay stale aggregate-nonce or partial-signature context, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/aggregator.rs::optimistic_payout
- Entrypoint: public gRPC `ClementineAggregator.OptimisticPayout` request
- Attacker controls: the order and replay timing of optimistic payout signing requests
- Exploit idea: defeat message-boundary assumptions around the order and replay timing of optimistic payout signing requests
- Invariant to test: optimistic payout retries must not replay stale aggregate-nonce or partial-signature context
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust test that replays or cross-binds optimistic payout signatures/nonces and assert the verifier/operator reject every mismatched tuple
