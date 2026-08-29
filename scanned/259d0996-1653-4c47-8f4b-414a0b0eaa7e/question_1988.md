# Q1988: find-collateral-amount via collateral-add: produce a price that passes `oracle-price-legal` while bei

## Question
Does `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) let an unprivileged attacker who controls the position's existing collateral and debt composition reach `find-collateral-amount` (mainnet/contracts/market/v0-4-market.clar:609) in a state where it produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? Given that it returns u0 for an absent asset, making a missing row indistinguishable from a zero holding, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:609` -> `find-collateral-amount`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: the position's existing collateral and debt composition
- Exploit idea: `find-collateral-amount` returns u0 for an absent asset, making a missing row indistinguishable from a zero holding. Reach it through `collateral-add` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `collateral-add` twice with the position's existing collateral and debt composition varied, and assert that the value `find-collateral-amount` returns is identical in both runs; a divergence confirms the finding.
