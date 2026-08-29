# Q0318: insert via collateral-remove: normalize a real holding to zero USD while the paired debt

## Question
Entering through `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) while controlling the set of assets held, can an unprivileged attacker make `insert` (mainnet/contracts/market/v0-market-vault.clar:159) normalize a real holding to zero USD while the paired debt normalizes upward? `insert` rewrites the whole registry entry for a user id, so the invariant that collateral is valued low and debt is valued high at every call site without exception would fail, yielding permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:159` -> `insert`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: the set of assets held
- Exploit idea: `insert` rewrites the whole registry entry for a user id. Reach it through `collateral-remove` and normalize a real holding to zero USD while the paired debt normalizes upward.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz the set of assets held across its boundary values through `collateral-remove` in simnet and assert `insert` never returns a value that breaks the invariant.
