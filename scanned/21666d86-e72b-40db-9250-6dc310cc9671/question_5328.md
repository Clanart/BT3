# Q5328: get-asset-value via supply-collateral-add: judge a position against an LTV belonging to a different a

## Question
Does `supply-collateral-add` (mainnet/contracts/market/v0-4-market.clar:1175) let an unprivileged attacker who controls the `ft` trait principal deciding which vault is routed to reach `get-asset-value` (mainnet/contracts/market/v0-4-market.clar:679) in a state where it judge a position against an LTV belonging to a different asset set? Given that it resolves a fresh price for a single asset and normalizes with a caller-supplied rounding direction, the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:679` -> `get-asset-value`
- Entrypoint: `supply-collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1175`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal deciding which vault is routed to
- Exploit idea: `get-asset-value` resolves a fresh price for a single asset and normalizes with a caller-supplied rounding direction. Reach it through `supply-collateral-add` and judge a position against an LTV belonging to a different asset set.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz the `ft` trait principal deciding which vault is routed to across its boundary values through `supply-collateral-add` in simnet and assert `get-asset-value` never returns a value that breaks the invariant.
