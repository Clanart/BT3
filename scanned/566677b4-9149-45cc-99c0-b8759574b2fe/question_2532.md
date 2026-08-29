# Q2532: get-asset-value via collateral-add: attach a price resolved for one asset to a different asset

## Question
Does `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) let an unprivileged attacker who controls `amount` reach `get-asset-value` (mainnet/contracts/market/v0-4-market.clar:679) in a state where it attach a price resolved for one asset to a different asset in the position? Given that it resolves a fresh price for a single asset and normalizes with a caller-supplied rounding direction, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:679` -> `get-asset-value`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `get-asset-value` resolves a fresh price for a single asset and normalizes with a caller-supplied rounding direction. Reach it through `collateral-add` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `amount` across its boundary values through `collateral-add` in simnet and assert `get-asset-value` never returns a value that breaks the invariant.
