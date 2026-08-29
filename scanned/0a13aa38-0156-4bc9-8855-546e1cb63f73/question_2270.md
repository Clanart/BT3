# Q2270: relevant via collateral-remove: normalize a real holding to zero USD while the paired debt

## Question
Entering through `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) while controlling whether the position has any enabled debt row (the has-debt branch), can an unprivileged attacker make `relevant` (mainnet/contracts/market/v0-market-vault.clar:175) normalize a real holding to zero USD while the paired debt normalizes upward? `relevant` drops any position row whose bit is not present in the enabled mask, so the invariant that a position that holds value can always be priced, and therefore always closed would fail, yielding protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:175` -> `relevant`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: whether the position has any enabled debt row (the has-debt branch)
- Exploit idea: `relevant` drops any position row whose bit is not present in the enabled mask. Reach it through `collateral-remove` and normalize a real holding to zero USD while the paired debt normalizes upward.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `collateral-remove` twice with whether the position has any enabled debt row (the has-debt branch) varied, and assert that the value `relevant` returns is identical in both runs; a divergence confirms the finding.
