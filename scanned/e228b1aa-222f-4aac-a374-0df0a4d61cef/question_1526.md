# Q1526: NEAR add_fast_transfer fast path changes fee semantics without changing proof identity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal state writer reached from public fast-finalization flows` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/lib.rs::add_fast_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast path changes fee semantics without changing proof identity` under persists relayer-sponsored fast-transfer state with `finalised = false` and reserves storage for later settlement, violating `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Target relayer-sponsored fast paths where the first leg is paid before the canonical proof arrives. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare claimed fee, relayer payout, and stored transfer fee across both legs and assert that the bridge never accepts a mismatch. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
