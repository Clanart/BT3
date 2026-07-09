# Q2533: NEAR migrated token swap path callback-bearing token flow exposes inconsistent intermediate state at boundary values

## Question
Can an unprivileged attacker trigger ``ft_on_transfer` branch for swapped migrated tokens` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::swap_migrated_token` violate `migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint` in the `callback-bearing token flow exposes inconsistent intermediate state` attack class because burns an old migrated token and mints the new mapped token inside a single bridge-controlled flow becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::swap_migrated_token`
- Entrypoint: ``ft_on_transfer` branch for swapped migrated tokens`
- Attacker controls: old token account, amount, sender, and migration mapping state
- Exploit idea: Target `ft_transfer_call`, ERC-1155 safe transfers, or custom-minter callbacks that occur before cleanup finishes. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint
- Expected Immunefi impact: Contract execution flows
- Fast validation: Instrument reentrant-capable receivers and assert that every externally-observable intermediate state is either harmless or replay-proof. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
