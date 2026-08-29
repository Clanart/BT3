# Q4672: iter-lookup-debt via collateral-remove: satisfy the freshness gate with a timestamp the gate was n

## Question
Does `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) let an unprivileged attacker who controls `receiver`, including a contract principal reach `iter-lookup-debt` (mainnet/contracts/market/v0-market-vault.clar:218) in a state where it satisfy the freshness gate with a timestamp the gate was never meant to accept? Given that it skips rows failing `relevant`, so a disabled asset's DEBT vanishes from the returned position, the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction breaks and the result is direct theft of user funds at rest or in motion.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:218` -> `iter-lookup-debt`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: `receiver`, including a contract principal
- Exploit idea: `iter-lookup-debt` skips rows failing `relevant`, so a disabled asset's DEBT vanishes from the returned position. Reach it through `collateral-remove` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: Set up the position in simnet, call `collateral-remove` with `receiver`, including a contract principal, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
