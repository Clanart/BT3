# Q0692: get-available-assets via transfer: attach a price resolved for one asset to a different asset

## Question
Does `transfer` (mainnet/contracts/vault/v0-vault-stx.clar:752) let an unprivileged attacker who controls `amount` reach `get-available-assets` (mainnet/contracts/vault/v0-vault-stx.clar:481) in a state where it attach a price resolved for one asset to a different asset in the position? Given that it reads real liquidity, a different quantity from the `assets` var that `redeem` also gates on, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:481` -> `get-available-assets`
- Entrypoint: `transfer` (`mainnet/contracts/vault/v0-vault-stx.clar:752`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `get-available-assets` reads real liquidity, a different quantity from the `assets` var that `redeem` also gates on. Reach it through `transfer` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `transfer` twice with `amount` varied, and assert that the value `get-available-assets` returns is identical in both runs; a divergence confirms the finding.
