# Q0140: mask-to-list-collateral via repay: judge a position against an LTV belonging to a different a

## Question
Does `repay` (mainnet/contracts/market/v0-4-market.clar:1316) let an unprivileged attacker who controls whether the repaid asset is in the accrued debt list reach `mask-to-list-collateral` (mainnet/contracts/market/v0-4-market.clar:449) in a state where it judge a position against an LTV belonging to a different asset set? Given that it expands a mask to a list of ids over ITER-UINT-64, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:449` -> `mask-to-list-collateral`
- Entrypoint: `repay` (`mainnet/contracts/market/v0-4-market.clar:1316`), unprivileged and publicly callable
- Attacker controls: whether the repaid asset is in the accrued debt list
- Exploit idea: `mask-to-list-collateral` expands a mask to a list of ids over ITER-UINT-64. Reach it through `repay` and judge a position against an LTV belonging to a different asset set.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `repay` twice with whether the repaid asset is in the accrued debt list varied, and assert that the value `mask-to-list-collateral` returns is identical in both runs; a divergence confirms the finding.
