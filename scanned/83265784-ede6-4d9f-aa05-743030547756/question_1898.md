# Q1898: debt-remove-scaled via liquidate: satisfy the freshness gate with a timestamp the gate was n

## Question
Entering through `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) while controlling `min-collateral-expected`, can an unprivileged attacker make `debt-remove-scaled` (mainnet/contracts/market/v0-market-vault.clar:473) satisfy the freshness gate with a timestamp the gate was never meant to accept? `debt-remove-scaled` clears the debt bit only when the remaining scaled debt is exactly zero, so the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call would fail, yielding permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:473` -> `debt-remove-scaled`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `min-collateral-expected`
- Exploit idea: `debt-remove-scaled` clears the debt bit only when the remaining scaled debt is exactly zero. Reach it through `liquidate` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `liquidate` twice with `min-collateral-expected` varied, and assert that the value `debt-remove-scaled` returns is identical in both runs; a divergence confirms the finding.
