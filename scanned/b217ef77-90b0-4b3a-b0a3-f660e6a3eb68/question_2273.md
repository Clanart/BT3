# Q2273: system-repay via repay: attach a price resolved for one asset to a different asset

## Question
Can an unprivileged attacker entering through `repay` (mainnet/contracts/market/v0-4-market.clar:1316), controlling the `ft` trait principal, drive `system-repay` (mainnet/contracts/vault/v0-vault-stx.clar:902) — which splits one payment with three different formulas for `principal-reduction`, `principal-repaid` and `interest-paid` — to attach a price resolved for one asset to a different asset in the position, breaking the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once, and cause protocol insolvency through uncollateralised debt?

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:902` -> `system-repay`
- Entrypoint: `repay` (`mainnet/contracts/market/v0-4-market.clar:1316`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `system-repay` splits one payment with three different formulas for `principal-reduction`, `principal-repaid` and `interest-paid`. Reach it through `repay` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Run the baseline `repay` call, then the attacker-shaped one with the `ft` trait principal, and assert the attacker's net token balance change is zero or negative.
