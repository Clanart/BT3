# Q2464: iter-find-superset via collateral-add: produce a price that passes `oracle-price-legal` while bei

## Question
Does `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) let an unprivileged attacker who controls call ordering within the block reach `iter-find-superset` (mainnet/contracts/registry/v0-egroup.clar:267) in a state where it produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? Given that it short-circuits on the first superset match, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is direct theft of another user's collateral.

## Target
- File/function: `mainnet/contracts/registry/v0-egroup.clar:267` -> `iter-find-superset`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: call ordering within the block
- Exploit idea: `iter-find-superset` short-circuits on the first superset match. Reach it through `collateral-add` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: Set up the position in simnet, call `collateral-add` with call ordering within the block, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
