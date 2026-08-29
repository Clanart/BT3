# Q5756: interpolate-rate via collateral-add: produce a price that passes `oracle-price-legal` while bei

## Question
Does `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) let an unprivileged attacker who controls the `ft` trait principal reach `interpolate-rate` (mainnet/contracts/vault/v0-vault-stx.clar:196) in a state where it produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? Given that it interpolates between packed u16 curve points, the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:196` -> `interpolate-rate`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `interpolate-rate` interpolates between packed u16 curve points. Reach it through `collateral-add` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `collateral-add` twice with the `ft` trait principal varied, and assert that the value `interpolate-rate` returns is identical in both runs; a divergence confirms the finding.
