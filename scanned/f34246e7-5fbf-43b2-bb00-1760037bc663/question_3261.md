# Q3261: NEAR migrated token swap path global asset-conservation invariant break

## Question
Can an unprivileged attacker combine the public surface behind ``ft_on_transfer` branch for swapped migrated tokens` with the code paths summarized by `near/omni-bridge/src/lib.rs::swap_migrated_token` and make total redeemable claims across chains exceed the total burned, locked, or custodied assets tracked by burns an old migrated token and mints the new mapped token inside a single bridge-controlled flow, violating `migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::swap_migrated_token`
- Entrypoint: ``ft_on_transfer` branch for swapped migrated tokens`
- Attacker controls: old token account, amount, sender, and migration mapping state
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class.
- Invariant to test: migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step.
