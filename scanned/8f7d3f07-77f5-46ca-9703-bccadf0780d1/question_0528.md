# Q0528: remove-user-collateral via repay: normalize a real holding to zero USD while the paired debt

## Question
Does `repay` (mainnet/contracts/market/v0-4-market.clar:1316) let an unprivileged attacker who controls whether the repaid asset is in the accrued debt list reach `remove-user-collateral` (mainnet/contracts/market/v0-market-vault.clar:205) in a state where it normalize a real holding to zero USD while the paired debt normalizes upward? Given that it asserts sufficiency then `map-delete`s only on an exact zero, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:205` -> `remove-user-collateral`
- Entrypoint: `repay` (`mainnet/contracts/market/v0-4-market.clar:1316`), unprivileged and publicly callable
- Attacker controls: whether the repaid asset is in the accrued debt list
- Exploit idea: `remove-user-collateral` asserts sufficiency then `map-delete`s only on an exact zero. Reach it through `repay` and normalize a real holding to zero USD while the paired debt normalizes upward.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz whether the repaid asset is in the accrued debt list across its boundary values through `repay` in simnet and assert `remove-user-collateral` never returns a value that breaks the invariant.
