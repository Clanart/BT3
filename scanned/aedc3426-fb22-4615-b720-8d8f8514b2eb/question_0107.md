# Q107: NEAR migrated token swap path migration swap leaves old and new claims live

## Question
Can an unprivileged attacker route value through ``ft_on_transfer` branch for swapped migrated tokens` so that `near/omni-bridge/src/lib.rs::swap_migrated_token` burns the old token but still leaves a live claim on the old path while minting the new token, violating `migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::swap_migrated_token`
- Entrypoint: ``ft_on_transfer` branch for swapped migrated tokens`
- Attacker controls: old token account, amount, sender, and migration mapping state
- Exploit idea: Target old/new token migration flows that combine bridge burning and replacement minting.
- Invariant to test: migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Track both token supplies and pending transfer state and assert that migrating one unit cannot leave two redeemable claims.
