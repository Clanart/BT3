# Q3978: NEAR resolve_fast_transfer mint-with-message path differs economically from plain mint through cross-module drift

## Question
Can an unprivileged attacker use `callback after `send_tokens` in the fast Near path` with control over token id, fast-transfer id, `ft_transfer_call` refund behavior, and the sent amount and desynchronize `near/omni-bridge/src/lib.rs::resolve_fast_transfer` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `mint-with-message path differs economically from plain mint` attack class because burns tokens for deployed assets and removes the fast-transfer state only when the callback indicates a refund-like path, violating `the callback result must not let attackers keep recipient funds while also preserving fast-transfer state or avoiding the compensating burn`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_fast_transfer`
- Entrypoint: `callback after `send_tokens` in the fast Near path`
- Attacker controls: token id, fast-transfer id, `ft_transfer_call` refund behavior, and the sent amount
- Exploit idea: Target bridge-token wrappers that mint to a temporary holder or rely on `ft_transfer_call`-style callbacks. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: the callback result must not let attackers keep recipient funds while also preserving fast-transfer state or avoiding the compensating burn
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare balances and state after every callback result and assert equivalence between message and no-message branches. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::resolve_fast_transfer` and the adjacent mint, burn, or custody accounting after every branch.
