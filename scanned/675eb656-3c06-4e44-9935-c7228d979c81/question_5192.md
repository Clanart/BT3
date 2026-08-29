# Q5192: get-liquidation-position via supply-collateral-add: make a required price path abort so the position can no lo

## Question
Does `supply-collateral-add` (mainnet/contracts/market/v0-4-market.clar:1175) let an unprivileged attacker who controls `min-shares` (the only slippage bound on the deposit leg) reach `get-liquidation-position` (mainnet/contracts/market/v0-4-market.clar:473) in a state where it make a required price path abort so the position can no longer be closed or seized? Given that it returns enabled collateral plus ALL debt, a different view from the one borrow validated against, the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:473` -> `get-liquidation-position`
- Entrypoint: `supply-collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1175`), unprivileged and publicly callable
- Attacker controls: `min-shares` (the only slippage bound on the deposit leg)
- Exploit idea: `get-liquidation-position` returns enabled collateral plus ALL debt, a different view from the one borrow validated against. Reach it through `supply-collateral-add` and make a required price path abort so the position can no longer be closed or seized.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `supply-collateral-add` twice with `min-shares` (the only slippage bound on the deposit leg) varied, and assert that the value `get-liquidation-position` returns is identical in both runs; a divergence confirms the finding.
