# Q1826: relevant via collateral-remove-redeem: normalize a real holding to zero USD while the paired debt

## Question
Entering through `collateral-remove-redeem` (mainnet/contracts/market/v0-4-market.clar:1211) while controlling `min-underlying`, can an unprivileged attacker make `relevant` (mainnet/contracts/market/v0-market-vault.clar:175) normalize a real holding to zero USD while the paired debt normalizes upward? `relevant` drops any position row whose bit is not present in the enabled mask, so the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call would fail, yielding protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:175` -> `relevant`
- Entrypoint: `collateral-remove-redeem` (`mainnet/contracts/market/v0-4-market.clar:1211`), unprivileged and publicly callable
- Attacker controls: `min-underlying`
- Exploit idea: `relevant` drops any position row whose bit is not present in the enabled mask. Reach it through `collateral-remove-redeem` and normalize a real holding to zero USD while the paired debt normalizes upward.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `collateral-remove-redeem` twice with `min-underlying` varied, and assert that the value `relevant` returns is identical in both runs; a divergence confirms the finding.
