# Q4220: mask-pos via collateral-remove-redeem: produce a price that passes `oracle-price-legal` while bei

## Question
Does `collateral-remove-redeem` (mainnet/contracts/market/v0-4-market.clar:1211) let an unprivileged attacker who controls the zToken/underlying id mapping reached (the u100 sentinel branch) reach `mask-pos` (mainnet/contracts/market/v0-market-vault.clar:91) in a state where it produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? Given that it maps an asset id to a bit position, offsetting debt bits by DEBT-OFFSET, the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:91` -> `mask-pos`
- Entrypoint: `collateral-remove-redeem` (`mainnet/contracts/market/v0-4-market.clar:1211`), unprivileged and publicly callable
- Attacker controls: the zToken/underlying id mapping reached (the u100 sentinel branch)
- Exploit idea: `mask-pos` maps an asset id to a bit position, offsetting debt bits by DEBT-OFFSET. Reach it through `collateral-remove-redeem` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `collateral-remove-redeem` twice with the zToken/underlying id mapping reached (the u100 sentinel branch) varied, and assert that the value `mask-pos` returns is identical in both runs; a divergence confirms the finding.
