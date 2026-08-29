# Q4970: refresh via collateral-add: attach a price resolved for one asset to a different asset

## Question
Entering through `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) while controlling the `ft` trait principal, can an unprivileged attacker make `refresh` (mainnet/contracts/market/v0-market-vault.clar:171) attach a price resolved for one asset to a different asset in the position? `refresh` rewrites `mask` and stamps `last-update` to `stacks-block-time` on every write, so the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once would fail, yielding protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:171` -> `refresh`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `refresh` rewrites `mask` and stamps `last-update` to `stacks-block-time` on every write. Reach it through `collateral-add` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `collateral-add` twice with the `ft` trait principal varied, and assert that the value `refresh` returns is identical in both runs; a divergence confirms the finding.
