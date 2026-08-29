# Q3655: calc-liq-collateral-repay via liquidate: attach a price resolved for one asset to a different asset

## Question
`calc-liq-collateral-repay` (mainnet/contracts/market/v0-4-market.clar:728) scales the repaid debt by `(+ BPS liq-penalty)`. Can an unprivileged caller of `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382), by choosing `collateral-receiver`, use that to attach a price resolved for one asset to a different asset in the position, violating the invariant that collateral is valued low and debt is valued high at every call site without exception and producing direct theft of user funds at rest or in motion?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:728` -> `calc-liq-collateral-repay`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `collateral-receiver`
- Exploit idea: `calc-liq-collateral-repay` scales the repaid debt by `(+ BPS liq-penalty)`. Reach it through `liquidate` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: In `local-testing/tests` on a local fork, drive `liquidate` with `collateral-receiver`, then read `calc-liq-collateral-repay` state before and after in the same block and assert the two sides of the invariant are equal.
