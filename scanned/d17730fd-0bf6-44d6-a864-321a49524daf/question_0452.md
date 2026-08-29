# Q0452: vault-system-borrow via liquidate: make a required price path abort so the position can no lo

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `debt-amount` reach `vault-system-borrow` (mainnet/contracts/market/v0-4-market.clar:198) in a state where it make a required price path abort so the position can no longer be closed or seized? Given that it routes a borrow to one of six vaults by asset id, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:198` -> `vault-system-borrow`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `debt-amount`
- Exploit idea: `vault-system-borrow` routes a borrow to one of six vaults by asset id. Reach it through `liquidate` and make a required price path abort so the position can no longer be closed or seized.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `liquidate` twice with `debt-amount` varied, and assert that the value `vault-system-borrow` returns is identical in both runs; a divergence confirms the finding.
