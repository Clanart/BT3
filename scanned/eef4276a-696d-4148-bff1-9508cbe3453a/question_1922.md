# Q1922: Race optimistic_payout_sign across concurrent state

## Question
Can an unprivileged attacker batch, retry, or reorder public `ClementineAggregator.OptimisticPayout` flow that reaches verifier optimistic payout signing interactions around the ECDSA verification signature supplied with the optimistic payout request so `optimistic_payout_sign` observes inconsistent state across memory, database, and on-chain checks, breaking the invariant that an optimistic payout signature must be domain-separated from every non-optimistic withdrawal path, and leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/verifier.rs::optimistic_payout_sign
- Entrypoint: public `ClementineAggregator.OptimisticPayout` flow that reaches verifier optimistic payout signing
- Attacker controls: the ECDSA verification signature supplied with the optimistic payout request
- Exploit idea: use retries, batching, or timing around the ECDSA verification signature supplied with the optimistic payout request to desynchronize state
- Invariant to test: an optimistic payout signature must be domain-separated from every non-optimistic withdrawal path
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust test that replays or cross-binds optimistic payout signatures/nonces and assert the verifier/operator reject every mismatched tuple
