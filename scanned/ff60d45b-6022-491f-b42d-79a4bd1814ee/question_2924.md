# Q2924: find via borrow: attach a price resolved for one asset to a different asset

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls the `price-feeds` buffers reach `find` (mainnet/contracts/registry/v0-assets.clar:135) in a state where it attach a price resolved for one asset to a different asset in the position? Given that it resolves an asset record from a principal through the `reverse` map, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/registry/v0-assets.clar:135` -> `find`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the `price-feeds` buffers
- Exploit idea: `find` resolves an asset record from a principal through the `reverse` map. Reach it through `borrow` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `borrow` twice with the `price-feeds` buffers varied, and assert that the value `find` returns is identical in both runs; a divergence confirms the finding.
