# Q3209: calc-final-liquidation-amounts via liquidate: produce a price that passes `oracle-price-legal` while bei

## Question
Can an unprivileged attacker entering through `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382), controlling `debt-amount`, drive `calc-final-liquidation-amounts` (mainnet/contracts/market/v0-4-market.clar:834) — which recomputes debt proportionally when collateral was capped, a SECOND re-derivation after `process-debt-asset` already capped once — to produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude, breaking the invariant that collateral is valued low and debt is valued high at every call site without exception, and cause protocol insolvency through uncollateralised debt?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:834` -> `calc-final-liquidation-amounts`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `debt-amount`
- Exploit idea: `calc-final-liquidation-amounts` recomputes debt proportionally when collateral was capped, a SECOND re-derivation after `process-debt-asset` already capped once. Reach it through `liquidate` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Run the baseline `liquidate` call, then the attacker-shaped one with `debt-amount`, and assert the attacker's net token balance change is zero or negative.
