# Q149: NEAR fast transfer status queries fast-transfer status changes in the wrong order

## Question
Can an unprivileged attacker trigger `public relayer-facing reads consumed by off-chain automation` so that `near/omni-bridge/src/lib.rs::get_fast_transfer_status / is_fast_transfer_finalised` marks, removes, or reuses fast-transfer state in an order that opens replay or fee-claim gaps, violating `observable fast-transfer state must correspond to one unambiguous economic state so relayers cannot act on stale statuses that the contract later interprets differently`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_fast_transfer_status / is_fast_transfer_finalised`
- Entrypoint: `public relayer-facing reads consumed by off-chain automation`
- Attacker controls: fast-transfer id choice and timing relative to claims or callbacks
- Exploit idea: Target state transitions among pending, finalised, removed, and claimed statuses across both legs.
- Invariant to test: observable fast-transfer state must correspond to one unambiguous economic state so relayers cannot act on stale statuses that the contract later interprets differently
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Drive all race orders between fast payout, canonical finalization, and fee claim and assert that each fast-transfer id follows one monotonic lifecycle.
