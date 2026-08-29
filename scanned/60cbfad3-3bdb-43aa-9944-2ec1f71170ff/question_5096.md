# Q5096: receive-tokens via liquidate-multi: attach a price resolved for one asset to a different asset

## Question
Does `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593) let an unprivileged attacker who controls the full batch list and its ordering reach `receive-tokens` (mainnet/contracts/market/v0-market-vault.clar:256) in a state where it attach a price resolved for one asset to a different asset in the position? Given that it pulls an asset from a named account, the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:256` -> `receive-tokens`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: the full batch list and its ordering
- Exploit idea: `receive-tokens` pulls an asset from a named account. Reach it through `liquidate-multi` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `liquidate-multi` twice with the full batch list and its ordering varied, and assert that the value `receive-tokens` returns is identical in both runs; a divergence confirms the finding.
