# Q0444: accrue-user-debts via call-ststx-ratio: normalize a real holding to zero USD while the paired debt

## Question
Does `call-ststx-ratio` (mainnet/contracts/market/v0-4-market.clar:1015) let an unprivileged attacker who controls whether the ratio is fetched before or after other state changes in the block reach `accrue-user-debts` (mainnet/contracts/market/v0-4-market.clar:259) in a state where it normalize a real holding to zero USD while the paired debt normalizes upward? Given that it folds accrual over the position's debt list only, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:259` -> `accrue-user-debts`
- Entrypoint: `call-ststx-ratio` (`mainnet/contracts/market/v0-4-market.clar:1015`), unprivileged and publicly callable
- Attacker controls: whether the ratio is fetched before or after other state changes in the block
- Exploit idea: `accrue-user-debts` folds accrual over the position's debt list only. Reach it through `call-ststx-ratio` and normalize a real holding to zero USD while the paired debt normalizes upward.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz whether the ratio is fetched before or after other state changes in the block across its boundary values through `call-ststx-ratio` in simnet and assert `accrue-user-debts` never returns a value that breaks the invariant.
