# Q2763: NEAR add_fast_transfer fast-transfer status changes in the wrong order via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal state writer reached from public fast-finalization flows` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/lib.rs::add_fast_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast-transfer status changes in the wrong order` under persists relayer-sponsored fast-transfer state with `finalised = false` and reserves storage for later settlement, violating `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Target state transitions among pending, finalised, removed, and claimed statuses across both legs. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Drive all race orders between fast payout, canonical finalization, and fee claim and assert that each fast-transfer id follows one monotonic lifecycle. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
