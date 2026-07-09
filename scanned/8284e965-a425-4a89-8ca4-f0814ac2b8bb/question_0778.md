# Q778: NEAR migrated token swap path mint-with-message path differs economically from plain mint

## Question
Can an unprivileged attacker trigger ``ft_on_transfer` branch for swapped migrated tokens` so that `near/omni-bridge/src/lib.rs::swap_migrated_token` mints through a callback-bearing path whose failure semantics differ from plain minting, violating `migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::swap_migrated_token`
- Entrypoint: ``ft_on_transfer` branch for swapped migrated tokens`
- Attacker controls: old token account, amount, sender, and migration mapping state
- Exploit idea: Target bridge-token wrappers that mint to a temporary holder or rely on `ft_transfer_call`-style callbacks.
- Invariant to test: migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare balances and state after every callback result and assert equivalence between message and no-message branches.
