# Q1110: linear-interpolate via collateral-add: produce a price that passes `oracle-price-legal` while bei

## Question
Entering through `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) while controlling the `ft` trait principal, can an unprivileged attacker make `linear-interpolate` (mainnet/contracts/vault/v0-vault-stx.clar:221) produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? `linear-interpolate` interpolates between two points, dividing by `(- x2 x1)`, so the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call would fail, yielding permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:221` -> `linear-interpolate`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `linear-interpolate` interpolates between two points, dividing by `(- x2 x1)`. Reach it through `collateral-add` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz the `ft` trait principal across its boundary values through `collateral-add` in simnet and assert `linear-interpolate` never returns a value that breaks the invariant.
