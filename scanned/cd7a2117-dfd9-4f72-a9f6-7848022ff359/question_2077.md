# Q2077: NEAR migrated token swap path callback-bearing token flow exposes inconsistent intermediate state

## Question
Can an unprivileged attacker exploit a callback-bearing branch in ``ft_on_transfer` branch for swapped migrated tokens` so that `near/omni-bridge/src/lib.rs::swap_migrated_token` exposes intermediate state that a receiver or token contract can act on inconsistently, violating `migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::swap_migrated_token`
- Entrypoint: ``ft_on_transfer` branch for swapped migrated tokens`
- Attacker controls: old token account, amount, sender, and migration mapping state
- Exploit idea: Target `ft_transfer_call`, ERC-1155 safe transfers, or custom-minter callbacks that occur before cleanup finishes.
- Invariant to test: migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint
- Expected Immunefi impact: Contract execution flows
- Fast validation: Instrument reentrant-capable receivers and assert that every externally-observable intermediate state is either harmless or replay-proof.
