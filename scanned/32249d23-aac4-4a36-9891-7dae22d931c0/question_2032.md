# Q2032: find-and-resolve-asset-value via liquidate-redeem: normalize a real holding to zero USD while the paired debt

## Question
Does `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604) let an unprivileged attacker who controls the redemption receiver reach `find-and-resolve-asset-value` (mainnet/contracts/market/v0-4-market.clar:668) in a state where it normalize a real holding to zero USD while the paired debt normalizes upward? Given that it reuses an already-resolved price from the asset list and returns u0 when the asset is not found, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is direct theft of another user's collateral.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:668` -> `find-and-resolve-asset-value`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the redemption receiver
- Exploit idea: `find-and-resolve-asset-value` reuses an already-resolved price from the asset list and returns u0 when the asset is not found. Reach it through `liquidate-redeem` and normalize a real holding to zero USD while the paired debt normalizes upward.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: Set up the position in simnet, call `liquidate-redeem` with the redemption receiver, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
