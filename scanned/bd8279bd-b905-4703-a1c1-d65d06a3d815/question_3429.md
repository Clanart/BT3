# Q3429: oracle-last-update via collateral-remove-redeem: make a required price path abort so the position can no lo

## Question
Can an unprivileged attacker entering through `collateral-remove-redeem` (mainnet/contracts/market/v0-4-market.clar:1211), controlling `min-underlying`, drive `oracle-last-update` (mainnet/contracts/market/v0-4-market.clar:939) — which returns the stored monotonic timestamp for a `{type, ident}` key shared by every asset using that feed — to make a required price path abort so the position can no longer be closed or seized, breaking the invariant that collateral is valued low and debt is valued high at every call site without exception, and cause temporary freezing of funds?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:939` -> `oracle-last-update`
- Entrypoint: `collateral-remove-redeem` (`mainnet/contracts/market/v0-4-market.clar:1211`), unprivileged and publicly callable
- Attacker controls: `min-underlying`
- Exploit idea: `oracle-last-update` returns the stored monotonic timestamp for a `{type, ident}` key shared by every asset using that feed. Reach it through `collateral-remove-redeem` and make a required price path abort so the position can no longer be closed or seized.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Snapshot every state variable `oracle-last-update` touches, run `collateral-remove-redeem` with `min-underlying`, recompute the invariant off-chain from the snapshot, and assert it matches the on-chain result.
