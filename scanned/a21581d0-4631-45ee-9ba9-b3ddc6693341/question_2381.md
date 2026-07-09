# Q2381: NEAR migrated token swap path callback-bearing token flow exposes inconsistent intermediate state through cross-module drift

## Question
Can an unprivileged attacker use ``ft_on_transfer` branch for swapped migrated tokens` with control over old token account, amount, sender, and migration mapping state and desynchronize `near/omni-bridge/src/lib.rs::swap_migrated_token` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `callback-bearing token flow exposes inconsistent intermediate state` attack class because burns an old migrated token and mints the new mapped token inside a single bridge-controlled flow, violating `migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::swap_migrated_token`
- Entrypoint: ``ft_on_transfer` branch for swapped migrated tokens`
- Attacker controls: old token account, amount, sender, and migration mapping state
- Exploit idea: Target `ft_transfer_call`, ERC-1155 safe transfers, or custom-minter callbacks that occur before cleanup finishes. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint
- Expected Immunefi impact: Contract execution flows
- Fast validation: Instrument reentrant-capable receivers and assert that every externally-observable intermediate state is either harmless or replay-proof. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::swap_migrated_token` and the adjacent mint, burn, or custody accounting after every branch.
