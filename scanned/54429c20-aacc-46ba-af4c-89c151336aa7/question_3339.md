# Q3339: NEAR add_fast_transfer fast-transfer storage refund reaches wrong party via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal state writer reached from public fast-finalization flows` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/lib.rs::add_fast_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast-transfer storage refund reaches wrong party` under persists relayer-sponsored fast-transfer state with `finalised = false` and reserves storage for later settlement, violating `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Target stored `storage_owner` values and removal paths that issue refunds after relayer activity. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Create relayer and user combinations and assert that every refund lands on the exact payer who financed that fast-transfer slot. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
