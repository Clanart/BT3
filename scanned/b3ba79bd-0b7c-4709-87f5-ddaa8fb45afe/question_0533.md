# Q0533: vault-accrue via collateral-remove-redeem: produce a price that passes `oracle-price-legal` while bei

## Question
Can an unprivileged attacker entering through `collateral-remove-redeem` (mainnet/contracts/market/v0-4-market.clar:1211), controlling `receiver` for the underlying leg, drive `vault-accrue` (mainnet/contracts/market/v0-4-market.clar:189) — which dispatches accrual to one of six vaults by asset id — to produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude, breaking the invariant that a resolved price passed the confidence and staleness gates in the form the gates were designed for, after every transform, and cause protocol insolvency?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:189` -> `vault-accrue`
- Entrypoint: `collateral-remove-redeem` (`mainnet/contracts/market/v0-4-market.clar:1211`), unprivileged and publicly callable
- Attacker controls: `receiver` for the underlying leg
- Exploit idea: `vault-accrue` dispatches accrual to one of six vaults by asset id. Reach it through `collateral-remove-redeem` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: a resolved price passed the confidence and staleness gates in the form the gates were designed for, after every transform
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Run the baseline `collateral-remove-redeem` call, then the attacker-shaped one with `receiver` for the underlying leg, and assert the attacker's net token balance change is zero or negative.
