# Q0712: write-feed via borrow: satisfy the freshness gate with a timestamp the gate was n

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls the `price-feeds` buffers reach `write-feed` (mainnet/contracts/market/v0-4-market.clar:129) in a state where it satisfy the freshness gate with a timestamp the gate was never meant to accept? Given that it applies one Pyth price-feed update and folds its status, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is direct theft of user funds at rest or in motion.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:129` -> `write-feed`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the `price-feeds` buffers
- Exploit idea: `write-feed` applies one Pyth price-feed update and folds its status. Reach it through `borrow` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: Set up the position in simnet, call `borrow` with the `price-feeds` buffers, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
