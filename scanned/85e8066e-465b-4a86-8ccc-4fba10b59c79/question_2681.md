# Q2681: convert-to-scaled-debt via collateral-remove: produce a price that passes `oracle-price-legal` while bei

## Question
Can an unprivileged attacker entering through `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107), controlling `amount` relative to the current collateral row (the removing-all branch), drive `convert-to-scaled-debt` (mainnet/contracts/market/v0-4-market.clar:648) — which scales a token amount by the cached borrow index, rounding up on the borrow path — to produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude, breaking the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once, and cause protocol insolvency through uncollateralised debt?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:648` -> `convert-to-scaled-debt`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: `amount` relative to the current collateral row (the removing-all branch)
- Exploit idea: `convert-to-scaled-debt` scales a token amount by the cached borrow index, rounding up on the borrow path. Reach it through `collateral-remove` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Run the baseline `collateral-remove` call, then the attacker-shaped one with `amount` relative to the current collateral row (the removing-all branch), and assert the attacker's net token balance change is zero or negative.
