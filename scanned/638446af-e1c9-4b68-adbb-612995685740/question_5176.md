# Q5176: convert-to-scaled-debt via liquidate: apply a transform after the gate that was supposed to boun

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `min-collateral-expected` reach `convert-to-scaled-debt` (mainnet/contracts/market/v0-4-market.clar:648) in a state where it apply a transform after the gate that was supposed to bound its output? Given that it scales a token amount by the cached borrow index, rounding up on the borrow path, the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once breaks and the result is direct theft of user funds at rest or in motion.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:648` -> `convert-to-scaled-debt`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `min-collateral-expected`
- Exploit idea: `convert-to-scaled-debt` scales a token amount by the cached borrow index, rounding up on the borrow path. Reach it through `liquidate` and apply a transform after the gate that was supposed to bound its output.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: Set up the position in simnet, call `liquidate` with `min-collateral-expected`, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
