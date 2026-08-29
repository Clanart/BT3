# Q5868: mask-pos via collateral-remove: produce a price that passes `oracle-price-legal` while bei

## Question
Does `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) let an unprivileged attacker who controls the `price-feeds` buffers reach `mask-pos` (mainnet/contracts/market/v0-market-vault.clar:91) in a state where it produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? Given that it maps an asset id to a bit position, offsetting debt bits by DEBT-OFFSET, the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:91` -> `mask-pos`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: the `price-feeds` buffers
- Exploit idea: `mask-pos` maps an asset id to a bit position, offsetting debt bits by DEBT-OFFSET. Reach it through `collateral-remove` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz the `price-feeds` buffers across its boundary values through `collateral-remove` in simnet and assert `mask-pos` never returns a value that breaks the invariant.
