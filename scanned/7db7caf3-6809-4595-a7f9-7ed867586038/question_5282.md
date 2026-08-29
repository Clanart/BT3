# Q5282: population via collateral-remove: normalize a real holding to zero USD while the paired debt

## Question
Entering through `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) while controlling `amount` relative to the current collateral row (the removing-all branch), can an unprivileged attacker make `population` (mainnet/contracts/registry/v0-egroup.clar:81) normalize a real holding to zero USD while the paired debt normalizes upward? `population` counts set bits to order the bucket search, so the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once would fail, yielding protocol insolvency.

## Target
- File/function: `mainnet/contracts/registry/v0-egroup.clar:81` -> `population`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: `amount` relative to the current collateral row (the removing-all branch)
- Exploit idea: `population` counts set bits to order the bucket search. Reach it through `collateral-remove` and normalize a real holding to zero USD while the paired debt normalizes upward.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `collateral-remove` twice with `amount` relative to the current collateral row (the removing-all branch) varied, and assert that the value `population` returns is identical in both runs; a divergence confirms the finding.
