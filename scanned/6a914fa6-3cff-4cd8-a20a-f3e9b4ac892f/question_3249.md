# Q3249: relevant via collateral-remove: attach a price resolved for one asset to a different asset

## Question
Can an unprivileged attacker entering through `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107), controlling the set of assets held, drive `relevant` (mainnet/contracts/market/v0-market-vault.clar:175) — which drops any position row whose bit is not present in the enabled mask — to attach a price resolved for one asset to a different asset in the position, breaking the invariant that collateral is valued low and debt is valued high at every call site without exception, and cause temporary freezing of funds?

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:175` -> `relevant`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: the set of assets held
- Exploit idea: `relevant` drops any position row whose bit is not present in the enabled mask. Reach it through `collateral-remove` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Snapshot every state variable `relevant` touches, run `collateral-remove` with the set of assets held, recompute the invariant off-chain from the snapshot, and assert it matches the on-chain result.
