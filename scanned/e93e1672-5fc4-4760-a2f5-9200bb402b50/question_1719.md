# Q1719: filter-out-debt-asset via supply-collateral-add: produce a price that passes `oracle-price-legal` while bei

## Question
`filter-out-debt-asset` (mainnet/contracts/market/v0-4-market.clar:633) rebuilds the debt list without one asset, under `as-max-len? ... u64`. Can an unprivileged caller of `supply-collateral-add` (mainnet/contracts/market/v0-4-market.clar:1175), by choosing the position state the final collateral-add is validated against, use that to produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude, violating the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction and producing temporary freezing of funds?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:633` -> `filter-out-debt-asset`
- Entrypoint: `supply-collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1175`), unprivileged and publicly callable
- Attacker controls: the position state the final collateral-add is validated against
- Exploit idea: `filter-out-debt-asset` rebuilds the debt list without one asset, under `as-max-len? ... u64`. Reach it through `supply-collateral-add` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Snapshot every state variable `filter-out-debt-asset` touches, run `supply-collateral-add` with the position state the final collateral-add is validated against, recompute the invariant off-chain from the snapshot, and assert it matches the on-chain result.
