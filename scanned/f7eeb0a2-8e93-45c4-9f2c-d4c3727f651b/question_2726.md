# Q2726: mask-pos via collateral-add: make a required price path abort so the position can no lo

## Question
Entering through `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) while controlling the position's existing collateral and debt composition, can an unprivileged attacker make `mask-pos` (mainnet/contracts/market/v0-market-vault.clar:91) make a required price path abort so the position can no longer be closed or seized? `mask-pos` maps an asset id to a bit position, offsetting debt bits by DEBT-OFFSET, so the invariant that a position that holds value can always be priced, and therefore always closed would fail, yielding protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:91` -> `mask-pos`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: the position's existing collateral and debt composition
- Exploit idea: `mask-pos` maps an asset id to a bit position, offsetting debt bits by DEBT-OFFSET. Reach it through `collateral-add` and make a required price path abort so the position can no longer be closed or seized.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `collateral-add` twice with the position's existing collateral and debt composition varied, and assert that the value `mask-pos` returns is identical in both runs; a divergence confirms the finding.
