# Q3666: NEAR migrated token swap path global asset-conservation invariant break at boundary values

## Question
Can an unprivileged attacker trigger ``ft_on_transfer` branch for swapped migrated tokens` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::swap_migrated_token` violate `migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint` in the `global asset-conservation invariant break` attack class because burns an old migrated token and mints the new mapped token inside a single bridge-controlled flow becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::swap_migrated_token`
- Entrypoint: ``ft_on_transfer` branch for swapped migrated tokens`
- Attacker controls: old token account, amount, sender, and migration mapping state
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
