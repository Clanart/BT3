# Q0722: mask-update via liquidate: produce a price that passes `oracle-price-legal` while bei

## Question
Entering through `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) while controlling `debt-amount`, can an unprivileged attacker make `mask-update` (mainnet/contracts/market/v0-market-vault.clar:94) produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? `mask-update` sets or clears one bit, clearing only when the row reaches exactly zero, so the invariant that collateral is valued low and debt is valued high at every call site without exception would fail, yielding protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:94` -> `mask-update`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `debt-amount`
- Exploit idea: `mask-update` sets or clears one bit, clearing only when the row reaches exactly zero. Reach it through `liquidate` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `liquidate` twice with `debt-amount` varied, and assert that the value `mask-update` returns is identical in both runs; a divergence confirms the finding.
