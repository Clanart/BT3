# Q0708: filter-out-debt-asset via liquidate: produce a price that passes `oracle-price-legal` while bei

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls the `price-feeds` buffers and their ordering reach `filter-out-debt-asset` (mainnet/contracts/market/v0-4-market.clar:633) in a state where it produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? Given that it rebuilds the debt list without one asset, under `as-max-len? ... u64`, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:633` -> `filter-out-debt-asset`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: the `price-feeds` buffers and their ordering
- Exploit idea: `filter-out-debt-asset` rebuilds the debt list without one asset, under `as-max-len? ... u64`. Reach it through `liquidate` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz the `price-feeds` buffers and their ordering across its boundary values through `liquidate` in simnet and assert `filter-out-debt-asset` never returns a value that breaks the invariant.
