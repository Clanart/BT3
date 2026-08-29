# Q2652: accrue-user-debts via liquidate-multi: judge a position against an LTV belonging to a different a

## Question
Does `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593) let an unprivileged attacker who controls how many entries share one price snapshot (price-feeds is passed as none) reach `accrue-user-debts` (mainnet/contracts/market/v0-4-market.clar:259) in a state where it judge a position against an LTV belonging to a different asset set? Given that it folds accrual over the position's debt list only, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:259` -> `accrue-user-debts`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: how many entries share one price snapshot (price-feeds is passed as none)
- Exploit idea: `accrue-user-debts` folds accrual over the position's debt list only. Reach it through `liquidate-multi` and judge a position against an LTV belonging to a different asset set.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz how many entries share one price snapshot (price-feeds is passed as none) across its boundary values through `liquidate-multi` in simnet and assert `accrue-user-debts` never returns a value that breaks the invariant.
