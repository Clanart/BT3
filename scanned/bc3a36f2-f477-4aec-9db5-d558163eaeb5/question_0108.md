# Q0108: filter-u128 via collateral-remove: normalize a real holding to zero USD while the paired debt

## Question
Does `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) let an unprivileged attacker who controls the set of assets held reach `filter-u128` (mainnet/contracts/registry/v0-egroup.clar:97) in a state where it normalize a real holding to zero USD while the paired debt normalizes upward? Given that it filters a 128-entry bucket list, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/registry/v0-egroup.clar:97` -> `filter-u128`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: the set of assets held
- Exploit idea: `filter-u128` filters a 128-entry bucket list. Reach it through `collateral-remove` and normalize a real holding to zero USD while the paired debt normalizes upward.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz the set of assets held across its boundary values through `collateral-remove` in simnet and assert `filter-u128` never returns a value that breaks the invariant.
