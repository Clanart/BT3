# Q3376: add-user-scaled-debt via repay: judge a position against an LTV belonging to a different a

## Question
Does `repay` (mainnet/contracts/market/v0-4-market.clar:1316) let an unprivileged attacker who controls the `ft` trait principal reach `add-user-scaled-debt` (mainnet/contracts/market/v0-market-vault.clar:237) in a state where it judge a position against an LTV belonging to a different asset set? Given that it adds to the scaled debt row with a graceful u0 default, the invariant that a resolved price passed the confidence and staleness gates in the form the gates were designed for, after every transform breaks and the result is direct theft of user funds at rest or in motion.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:237` -> `add-user-scaled-debt`
- Entrypoint: `repay` (`mainnet/contracts/market/v0-4-market.clar:1316`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `add-user-scaled-debt` adds to the scaled debt row with a graceful u0 default. Reach it through `repay` and judge a position against an LTV belonging to a different asset set.
- Invariant to test: a resolved price passed the confidence and staleness gates in the form the gates were designed for, after every transform
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: Set up the position in simnet, call `repay` with the `ft` trait principal, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
