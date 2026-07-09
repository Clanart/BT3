# Q1921: NEAR migrated token swap path custody accounting diverges from wrapped supply at boundary values

## Question
Can an unprivileged attacker trigger ``ft_on_transfer` branch for swapped migrated tokens` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::swap_migrated_token` violate `migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint` in the `custody accounting diverges from wrapped supply` attack class because burns an old migrated token and mints the new mapped token inside a single bridge-controlled flow becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::swap_migrated_token`
- Entrypoint: ``ft_on_transfer` branch for swapped migrated tokens`
- Attacker controls: old token account, amount, sender, and migration mapping state
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
