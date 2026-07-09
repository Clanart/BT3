# Q3122: NEAR migrated token swap path different callback outcomes produce the same user-visible success at boundary values

## Question
Can an unprivileged attacker trigger ``ft_on_transfer` branch for swapped migrated tokens` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::swap_migrated_token` violate `migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint` in the `different callback outcomes produce the same user-visible success` attack class because burns an old migrated token and mints the new mapped token inside a single bridge-controlled flow becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::swap_migrated_token`
- Entrypoint: ``ft_on_transfer` branch for swapped migrated tokens`
- Attacker controls: old token account, amount, sender, and migration mapping state
- Exploit idea: Target branches that interpret callback bytes leniently or default to success-like behavior on malformed returns. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint
- Expected Immunefi impact: Contract execution flows
- Fast validation: Enumerate all callback result shapes and assert one unique mapping from callback outcome to bridge state transition. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
