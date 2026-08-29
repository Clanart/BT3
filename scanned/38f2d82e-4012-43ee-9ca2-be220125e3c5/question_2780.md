# Q2780: get-liquidation-position via liquidate-redeem: apply a transform after the gate that was supposed to boun

## Question
Does `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604) let an unprivileged attacker who controls the redemption receiver reach `get-liquidation-position` (mainnet/contracts/market/v0-4-market.clar:473) in a state where it apply a transform after the gate that was supposed to bound its output? Given that it returns enabled collateral plus ALL debt, a different view from the one borrow validated against, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:473` -> `get-liquidation-position`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the redemption receiver
- Exploit idea: `get-liquidation-position` returns enabled collateral plus ALL debt, a different view from the one borrow validated against. Reach it through `liquidate-redeem` and apply a transform after the gate that was supposed to bound its output.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `liquidate-redeem` twice with the redemption receiver varied, and assert that the value `get-liquidation-position` returns is identical in both runs; a divergence confirms the finding.
