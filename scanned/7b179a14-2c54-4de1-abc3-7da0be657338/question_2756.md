# Q2756: vault-system-repay via liquidate: attach a price resolved for one asset to a different asset

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `collateral-receiver` reach `vault-system-repay` (mainnet/contracts/market/v0-4-market.clar:207) in a state where it attach a price resolved for one asset to a different asset in the position? Given that it routes a repayment to one of six vaults by asset id, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:207` -> `vault-system-repay`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `collateral-receiver`
- Exploit idea: `vault-system-repay` routes a repayment to one of six vaults by asset id. Reach it through `liquidate` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `liquidate` twice with `collateral-receiver` varied, and assert that the value `vault-system-repay` returns is identical in both runs; a divergence confirms the finding.
