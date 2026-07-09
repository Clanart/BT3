# Q2828: NEAR migrated token swap path different callback outcomes produce the same user-visible success via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through ``ft_on_transfer` branch for swapped migrated tokens` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::swap_migrated_token` ends up accepting two inconsistent interpretations of the same economic event specifically around `different callback outcomes produce the same user-visible success` under burns an old migrated token and mints the new mapped token inside a single bridge-controlled flow, violating `migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::swap_migrated_token`
- Entrypoint: ``ft_on_transfer` branch for swapped migrated tokens`
- Attacker controls: old token account, amount, sender, and migration mapping state
- Exploit idea: Target branches that interpret callback bytes leniently or default to success-like behavior on malformed returns. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint
- Expected Immunefi impact: Contract execution flows
- Fast validation: Enumerate all callback result shapes and assert one unique mapping from callback outcome to bridge state transition. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
