# Q1428: Misbind trusted context inside optimistic_payout

## Question
Can an unprivileged attacker reach `optimistic_payout` through public gRPC `ClementineAggregator.OptimisticPayout` request and make attacker-controlled the ECDSA verification signature supplied with the optimistic payout request bind to the wrong trusted context, so the partial-signature context attached to a payout request is interpreted for one bridge action while authorizing another, violating the invariant that partial signatures must stay bound to the exact optimistic payout tuple that was reviewed, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/rpc/aggregator.rs::optimistic_payout
- Entrypoint: public gRPC `ClementineAggregator.OptimisticPayout` request
- Attacker controls: the ECDSA verification signature supplied with the optimistic payout request
- Exploit idea: bind attacker-controlled the ECDSA verification signature supplied with the optimistic payout request to the wrong trusted bridge context
- Invariant to test: partial signatures must stay bound to the exact optimistic payout tuple that was reviewed
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust test that replays or cross-binds optimistic payout signatures/nonces and assert the verifier/operator reject every mismatched tuple
