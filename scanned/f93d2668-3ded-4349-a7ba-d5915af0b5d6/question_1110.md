# Q1110: NEAR migrated token swap path mint-with-message path differs economically from plain mint through cross-module drift

## Question
Can an unprivileged attacker use ``ft_on_transfer` branch for swapped migrated tokens` with control over old token account, amount, sender, and migration mapping state and desynchronize `near/omni-bridge/src/lib.rs::swap_migrated_token` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `mint-with-message path differs economically from plain mint` attack class because burns an old migrated token and mints the new mapped token inside a single bridge-controlled flow, violating `migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::swap_migrated_token`
- Entrypoint: ``ft_on_transfer` branch for swapped migrated tokens`
- Attacker controls: old token account, amount, sender, and migration mapping state
- Exploit idea: Target bridge-token wrappers that mint to a temporary holder or rely on `ft_transfer_call`-style callbacks. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: migration swaps must preserve one-to-one value and must never let a user escape the burn leg while still receiving the replacement mint
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare balances and state after every callback result and assert equivalence between message and no-message branches. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::swap_migrated_token` and the adjacent mint, burn, or custody accounting after every branch.
