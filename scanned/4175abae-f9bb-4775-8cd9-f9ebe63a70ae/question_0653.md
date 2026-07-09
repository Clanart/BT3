# Q653: NEAR fast transfer status queries fast-transfer status changes in the wrong order at boundary values

## Question
Can an unprivileged attacker trigger `public relayer-facing reads consumed by off-chain automation` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::get_fast_transfer_status / is_fast_transfer_finalised` violate `observable fast-transfer state must correspond to one unambiguous economic state so relayers cannot act on stale statuses that the contract later interprets differently` in the `fast-transfer status changes in the wrong order` attack class because exposes whether a fast transfer exists and whether it is marked finalised becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_fast_transfer_status / is_fast_transfer_finalised`
- Entrypoint: `public relayer-facing reads consumed by off-chain automation`
- Attacker controls: fast-transfer id choice and timing relative to claims or callbacks
- Exploit idea: Target state transitions among pending, finalised, removed, and claimed statuses across both legs. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: observable fast-transfer state must correspond to one unambiguous economic state so relayers cannot act on stale statuses that the contract later interprets differently
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Drive all race orders between fast payout, canonical finalization, and fee claim and assert that each fast-transfer id follows one monotonic lifecycle. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
