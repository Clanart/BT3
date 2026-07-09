# Q317: NEAR fast transfer status queries fast-transfer status changes in the wrong order via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public relayer-facing reads consumed by off-chain automation` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/lib.rs::get_fast_transfer_status / is_fast_transfer_finalised` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast-transfer status changes in the wrong order` under exposes whether a fast transfer exists and whether it is marked finalised, violating `observable fast-transfer state must correspond to one unambiguous economic state so relayers cannot act on stale statuses that the contract later interprets differently`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_fast_transfer_status / is_fast_transfer_finalised`
- Entrypoint: `public relayer-facing reads consumed by off-chain automation`
- Attacker controls: fast-transfer id choice and timing relative to claims or callbacks
- Exploit idea: Target state transitions among pending, finalised, removed, and claimed statuses across both legs. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: observable fast-transfer state must correspond to one unambiguous economic state so relayers cannot act on stale statuses that the contract later interprets differently
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Drive all race orders between fast payout, canonical finalization, and fee claim and assert that each fast-transfer id follows one monotonic lifecycle. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
