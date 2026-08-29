# Q0560: resolve via liquidate: judge a position against an LTV belonging to a different a

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `debt-amount` reach `resolve` (mainnet/contracts/registry/v0-egroup.clar:360) in a state where it judge a position against an LTV belonging to a different asset set? Given that it selects the efficiency group for a position mask, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/registry/v0-egroup.clar:360` -> `resolve`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `debt-amount`
- Exploit idea: `resolve` selects the efficiency group for a position mask. Reach it through `liquidate` and judge a position against an LTV belonging to a different asset set.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `liquidate` twice with `debt-amount` varied, and assert that the value `resolve` returns is identical in both runs; a divergence confirms the finding.
