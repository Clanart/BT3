# Q2574: get-egroup via liquidate: attach a price resolved for one asset to a different asset

## Question
Entering through `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) while controlling `debt-amount`, can an unprivileged attacker make `get-egroup` (mainnet/contracts/market/v0-4-market.clar:460) attach a price resolved for one asset to a different asset in the position? `get-egroup` resolves the efficiency group for a mask and is unwrapped with `try!` on every health path, so the invariant that a position that holds value can always be priced, and therefore always closed would fail, yielding permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:460` -> `get-egroup`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `debt-amount`
- Exploit idea: `get-egroup` resolves the efficiency group for a mask and is unwrapped with `try!` on every health path. Reach it through `liquidate` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz `debt-amount` across its boundary values through `liquidate` in simnet and assert `get-egroup` never returns a value that breaks the invariant.
