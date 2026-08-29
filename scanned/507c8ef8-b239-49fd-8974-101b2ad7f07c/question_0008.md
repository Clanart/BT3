# Q0008: get-notional-evaluation via liquidate-multi: produce a price that passes `oracle-price-legal` while bei

## Question
Does `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593) let an unprivileged attacker who controls the trait principals supplied per entry reach `get-notional-evaluation` (mainnet/contracts/market/v0-4-market.clar:514) in a state where it produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? Given that it folds over the ENABLED asset list, so a position row whose asset is absent from that list contributes nothing to either total, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:514` -> `get-notional-evaluation`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: the trait principals supplied per entry
- Exploit idea: `get-notional-evaluation` folds over the ENABLED asset list, so a position row whose asset is absent from that list contributes nothing to either total. Reach it through `liquidate-multi` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `liquidate-multi` twice with the trait principals supplied per entry varied, and assert that the value `get-notional-evaluation` returns is identical in both runs; a divergence confirms the finding.
