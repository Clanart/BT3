# Q4432: oracle-price-legal via collateral-add: normalize a real holding to zero USD while the paired debt

## Question
Does `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) let an unprivileged attacker who controls `amount` reach `oracle-price-legal` (mainnet/contracts/market/v0-4-market.clar:362) in a state where it normalize a real holding to zero USD while the paired debt normalizes upward? Given that it accepts any price strictly greater than zero, with no upper bound and no sanity band, the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction breaks and the result is direct theft of another user's collateral.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:362` -> `oracle-price-legal`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `oracle-price-legal` accepts any price strictly greater than zero, with no upper bound and no sanity band. Reach it through `collateral-add` and normalize a real holding to zero USD while the paired debt normalizes upward.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: Set up the position in simnet, call `collateral-add` with `amount`, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
