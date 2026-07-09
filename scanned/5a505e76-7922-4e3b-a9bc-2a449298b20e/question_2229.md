# Q2229: NEAR migrated token swap path callback-bearing token flow exposes inconsistent intermediate state via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through ``ft_on_transfer` branch for swapped migrated tokens` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::swap_migrated_token` ends up accepting two inconsistent interpretations of the same economic event specifically around `callback-bearing token flow exposes inconsistent intermediate state` under burns an old migrated token and mints the new mapped token inside a single bridge-controlled flow, violating `migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::swap_migrated_token`
- Entrypoint: ``ft_on_transfer` branch for swapped migrated tokens`
- Attacker controls: old token account, amount, sender, and migration mapping state
- Exploit idea: Target `ft_transfer_call`, ERC-1155 safe transfers, or custom-minter callbacks that occur before cleanup finishes. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint
- Expected Immunefi impact: Contract execution flows
- Fast validation: Instrument reentrant-capable receivers and assert that every externally-observable intermediate state is either harmless or replay-proof. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
