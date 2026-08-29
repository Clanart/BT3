# Q5788: get-account-scaled-debt via repay: satisfy the freshness gate with a timestamp the gate was n

## Question
Does `repay` (mainnet/contracts/market/v0-4-market.clar:1316) let an unprivileged attacker who controls `amount`, including far above the real debt (the capping path) reach `get-account-scaled-debt` (mainnet/contracts/market/v0-market-vault.clar:307) in a state where it satisfy the freshness gate with a timestamp the gate was never meant to accept? Given that it reads one scaled debt row, the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once breaks and the result is direct theft of user funds at rest or in motion.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:307` -> `get-account-scaled-debt`
- Entrypoint: `repay` (`mainnet/contracts/market/v0-4-market.clar:1316`), unprivileged and publicly callable
- Attacker controls: `amount`, including far above the real debt (the capping path)
- Exploit idea: `get-account-scaled-debt` reads one scaled debt row. Reach it through `repay` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: Set up the position in simnet, call `repay` with `amount`, including far above the real debt (the capping path), and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
