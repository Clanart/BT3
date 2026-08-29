# Q0868: get-cached-indexes via redeem: judge a position against an LTV belonging to a different a

## Question
Does `redeem` (mainnet/contracts/vault/v0-vault-stx.clar:797) let an unprivileged attacker who controls `recipient` reach `get-cached-indexes` (mainnet/contracts/market/v0-4-market.clar:944) in a state where it judge a position against an LTV belonging to a different asset set? Given that it reads the per-block cache entry that `resolve-ztoken` and the debt conversions depend on, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is direct theft of another user's collateral.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:944` -> `get-cached-indexes`
- Entrypoint: `redeem` (`mainnet/contracts/vault/v0-vault-stx.clar:797`), unprivileged and publicly callable
- Attacker controls: `recipient`
- Exploit idea: `get-cached-indexes` reads the per-block cache entry that `resolve-ztoken` and the debt conversions depend on. Reach it through `redeem` and judge a position against an LTV belonging to a different asset set.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: Set up the position in simnet, call `redeem` with `recipient`, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
