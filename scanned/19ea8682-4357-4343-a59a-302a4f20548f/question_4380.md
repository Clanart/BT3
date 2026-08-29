# Q4380: oracle-price-legal via liquidate-redeem: judge a position against an LTV belonging to a different a

## Question
Does `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604) let an unprivileged attacker who controls the borrower targeted reach `oracle-price-legal` (mainnet/contracts/market/v0-4-market.clar:362) in a state where it judge a position against an LTV belonging to a different asset set? Given that it accepts any price strictly greater than zero, with no upper bound and no sanity band, the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:362` -> `oracle-price-legal`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the borrower targeted
- Exploit idea: `oracle-price-legal` accepts any price strictly greater than zero, with no upper bound and no sanity band. Reach it through `liquidate-redeem` and judge a position against an LTV belonging to a different asset set.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz the borrower targeted across its boundary values through `liquidate-redeem` in simnet and assert `oracle-price-legal` never returns a value that breaks the invariant.
