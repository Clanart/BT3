# Q0246: accrue-user-collateral via supply-collateral-add: produce a price that passes `oracle-price-legal` while bei

## Question
Entering through `supply-collateral-add` (mainnet/contracts/market/v0-4-market.clar:1175) while controlling `amount`, can an unprivileged attacker make `accrue-user-collateral` (mainnet/contracts/market/v0-4-market.clar:270) produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? `accrue-user-collateral` accrues only rows that `is-ztoken` recognises, skipping everything else, so the invariant that collateral is valued low and debt is valued high at every call site without exception would fail, yielding permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:270` -> `accrue-user-collateral`
- Entrypoint: `supply-collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1175`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `accrue-user-collateral` accrues only rows that `is-ztoken` recognises, skipping everything else. Reach it through `supply-collateral-add` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `amount` across its boundary values through `supply-collateral-add` in simnet and assert `accrue-user-collateral` never returns a value that breaks the invariant.
