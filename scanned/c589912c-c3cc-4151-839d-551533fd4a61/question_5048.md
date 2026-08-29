# Q5048: accrue-user-collateral via borrow: judge a position against an LTV belonging to a different a

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls `amount` reach `accrue-user-collateral` (mainnet/contracts/market/v0-4-market.clar:270) in a state where it judge a position against an LTV belonging to a different asset set? Given that it accrues only rows that `is-ztoken` recognises, skipping everything else, the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:270` -> `accrue-user-collateral`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `accrue-user-collateral` accrues only rows that `is-ztoken` recognises, skipping everything else. Reach it through `borrow` and judge a position against an LTV belonging to a different asset set.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `borrow` twice with `amount` varied, and assert that the value `accrue-user-collateral` returns is identical in both runs; a divergence confirms the finding.
