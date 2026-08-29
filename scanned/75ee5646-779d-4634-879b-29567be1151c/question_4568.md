# Q4568: interest-rate via collateral-remove: attach a price resolved for one asset to a different asset

## Question
Does `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) let an unprivileged attacker who controls the set of assets held reach `interest-rate` (mainnet/contracts/vault/v0-vault-stx.clar:371) in a state where it attach a price resolved for one asset to a different asset in the position? Given that it interpolates the packed curve at the current utilization, the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:371` -> `interest-rate`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: the set of assets held
- Exploit idea: `interest-rate` interpolates the packed curve at the current utilization. Reach it through `collateral-remove` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `collateral-remove` twice with the set of assets held varied, and assert that the value `interest-rate` returns is identical in both runs; a divergence confirms the finding.
