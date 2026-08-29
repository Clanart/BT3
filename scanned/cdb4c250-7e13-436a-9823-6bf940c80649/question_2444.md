# Q2444: iter-lookup-collateral via liquidate: apply a transform after the gate that was supposed to boun

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `collateral-receiver` reach `iter-lookup-collateral` (mainnet/contracts/market/v0-market-vault.clar:180) in a state where it apply a transform after the gate that was supposed to bound its output? Given that it skips rows failing `relevant`, so a disabled asset's collateral vanishes from the returned position, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:180` -> `iter-lookup-collateral`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `collateral-receiver`
- Exploit idea: `iter-lookup-collateral` skips rows failing `relevant`, so a disabled asset's collateral vanishes from the returned position. Reach it through `liquidate` and apply a transform after the gate that was supposed to bound its output.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `liquidate` twice with `collateral-receiver` varied, and assert that the value `iter-lookup-collateral` returns is identical in both runs; a divergence confirms the finding.
