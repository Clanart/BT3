# Q944: NEAR migrated token swap path mint-with-message path differs economically from plain mint via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through ``ft_on_transfer` branch for swapped migrated tokens` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::swap_migrated_token` ends up accepting two inconsistent interpretations of the same economic event specifically around `mint-with-message path differs economically from plain mint` under burns an old migrated token and mints the new mapped token inside a single bridge-controlled flow, violating `migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::swap_migrated_token`
- Entrypoint: ``ft_on_transfer` branch for swapped migrated tokens`
- Attacker controls: old token account, amount, sender, and migration mapping state
- Exploit idea: Target bridge-token wrappers that mint to a temporary holder or rely on `ft_transfer_call`-style callbacks. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare balances and state after every callback result and assert equivalence between message and no-message branches. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
