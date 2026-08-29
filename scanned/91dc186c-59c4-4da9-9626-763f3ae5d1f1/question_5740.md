# Q5740: uint-to-list-u64 via borrow: attach a price resolved for one asset to a different asset

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls the `ft` trait principal reach `uint-to-list-u64` (mainnet/contracts/registry/v0-assets.clar:80) in a state where it attach a price resolved for one asset to a different asset in the position? Given that it expands a bitmap into a 64-element list, the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once breaks and the result is theft of unclaimed yield.

## Target
- File/function: `mainnet/contracts/registry/v0-assets.clar:80` -> `uint-to-list-u64`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `uint-to-list-u64` expands a bitmap into a 64-element list. Reach it through `borrow` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: High - theft of unclaimed yield
- Fast validation: Set up the position in simnet, call `borrow` with the `ft` trait principal, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
