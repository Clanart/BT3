# Q2183: find-debt-scaled via collateral-add: judge a position against an LTV belonging to a different a

## Question
`find-debt-scaled` (mainnet/contracts/market/v0-4-market.clar:621) returns u0 for an absent asset, making a missing debt row indistinguishable from no debt. Can an unprivileged caller of `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020), by choosing the `ft` trait principal, use that to judge a position against an LTV belonging to a different asset set, violating the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once and producing protocol insolvency through uncollateralised debt?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:621` -> `find-debt-scaled`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `find-debt-scaled` returns u0 for an absent asset, making a missing debt row indistinguishable from no debt. Reach it through `collateral-add` and judge a position against an LTV belonging to a different asset set.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Run the baseline `collateral-add` call, then the attacker-shaped one with the `ft` trait principal, and assert the attacker's net token balance change is zero or negative.
