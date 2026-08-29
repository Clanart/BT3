# Q1208: iter-lookup-collateral via borrow: produce a price that passes `oracle-price-legal` while bei

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls the `ft` trait principal reach `iter-lookup-collateral` (mainnet/contracts/market/v0-market-vault.clar:180) in a state where it produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? Given that it skips rows failing `relevant`, so a disabled asset's collateral vanishes from the returned position, the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:180` -> `iter-lookup-collateral`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `iter-lookup-collateral` skips rows failing `relevant`, so a disabled asset's collateral vanishes from the returned position. Reach it through `borrow` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `borrow` twice with the `ft` trait principal varied, and assert that the value `iter-lookup-collateral` returns is identical in both runs; a divergence confirms the finding.
