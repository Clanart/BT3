# Q1364: subset via collateral-remove: normalize a real holding to zero USD while the paired debt

## Question
Does `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) let an unprivileged attacker who controls the `ft` trait principal reach `subset` (mainnet/contracts/market/v0-market-vault.clar:100) in a state where it normalize a real holding to zero USD while the paired debt normalizes upward? Given that it tests bitmask containment, the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:100` -> `subset`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `subset` tests bitmask containment. Reach it through `collateral-remove` and normalize a real holding to zero USD while the paired debt normalizes upward.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `collateral-remove` twice with the `ft` trait principal varied, and assert that the value `subset` returns is identical in both runs; a divergence confirms the finding.
