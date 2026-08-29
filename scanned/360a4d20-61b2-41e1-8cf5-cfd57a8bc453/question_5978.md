# Q5978: subset via supply-collateral-add: normalize a real holding to zero USD while the paired debt

## Question
Entering through `supply-collateral-add` (mainnet/contracts/market/v0-4-market.clar:1175) while controlling vault share price at the moment of the deposit leg, can an unprivileged attacker make `subset` (mainnet/contracts/market/v0-market-vault.clar:100) normalize a real holding to zero USD while the paired debt normalizes upward? `subset` tests bitmask containment, so the invariant that collateral is valued low and debt is valued high at every call site without exception would fail, yielding protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:100` -> `subset`
- Entrypoint: `supply-collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1175`), unprivileged and publicly callable
- Attacker controls: vault share price at the moment of the deposit leg
- Exploit idea: `subset` tests bitmask containment. Reach it through `supply-collateral-add` and normalize a real holding to zero USD while the paired debt normalizes upward.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `supply-collateral-add` twice with vault share price at the moment of the deposit leg varied, and assert that the value `subset` returns is identical in both runs; a divergence confirms the finding.
