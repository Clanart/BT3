# Q3869: NEAR add_fast_transfer second-leg fee claim ignores first-leg mismatch via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal state writer reached from public fast-finalization flows` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/lib.rs::add_fast_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `second-leg fee claim ignores first-leg mismatch` under persists relayer-sponsored fast-transfer state with `finalised = false` and reserves storage for later settlement, violating `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Target comparisons between stored fast-transfer state and canonical first-leg proof during fee claim. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Balance manipulation
- Fast validation: Vary principal, fee, recipient, and message between legs and assert that any mismatch blocks fee release and leaves replay state consistent. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
