# Q2058: filter-u128 via collateral-add: produce a price that passes `oracle-price-legal` while bei

## Question
Entering through `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) while controlling `amount`, can an unprivileged attacker make `filter-u128` (mainnet/contracts/registry/v0-egroup.clar:97) produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? `filter-u128` filters a 128-entry bucket list, so the invariant that a position that holds value can always be priced, and therefore always closed would fail, yielding permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/registry/v0-egroup.clar:97` -> `filter-u128`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `filter-u128` filters a 128-entry bucket list. Reach it through `collateral-add` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz `amount` across its boundary values through `collateral-add` in simnet and assert `filter-u128` never returns a value that breaks the invariant.
