# Q2916: get-egroup via collateral-add: satisfy the freshness gate with a timestamp the gate was n

## Question
Does `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) let an unprivileged attacker who controls `amount` reach `get-egroup` (mainnet/contracts/market/v0-4-market.clar:460) in a state where it satisfy the freshness gate with a timestamp the gate was never meant to accept? Given that it resolves the efficiency group for a mask and is unwrapped with `try!` on every health path, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:460` -> `get-egroup`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `get-egroup` resolves the efficiency group for a mask and is unwrapped with `try!` on every health path. Reach it through `collateral-add` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz `amount` across its boundary values through `collateral-add` in simnet and assert `get-egroup` never returns a value that breaks the invariant.
