# Q3164: insert via collateral-remove-redeem: satisfy the freshness gate with a timestamp the gate was n

## Question
Does `collateral-remove-redeem` (mainnet/contracts/market/v0-4-market.clar:1211) let an unprivileged attacker who controls `min-underlying` reach `insert` (mainnet/contracts/market/v0-market-vault.clar:159) in a state where it satisfy the freshness gate with a timestamp the gate was never meant to accept? Given that it rewrites the whole registry entry for a user id, the invariant that a resolved price passed the confidence and staleness gates in the form the gates were designed for, after every transform breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:159` -> `insert`
- Entrypoint: `collateral-remove-redeem` (`mainnet/contracts/market/v0-4-market.clar:1211`), unprivileged and publicly callable
- Attacker controls: `min-underlying`
- Exploit idea: `insert` rewrites the whole registry entry for a user id. Reach it through `collateral-remove-redeem` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: a resolved price passed the confidence and staleness gates in the form the gates were designed for, after every transform
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `collateral-remove-redeem` twice with `min-underlying` varied, and assert that the value `insert` returns is identical in both runs; a divergence confirms the finding.
