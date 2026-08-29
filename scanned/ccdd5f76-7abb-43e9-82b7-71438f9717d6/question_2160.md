# Q2160: create via collateral-remove-redeem: satisfy the freshness gate with a timestamp the gate was n

## Question
Does `collateral-remove-redeem` (mainnet/contracts/market/v0-4-market.clar:1211) let an unprivileged attacker who controls `min-underlying` reach `create` (mainnet/contracts/market/v0-market-vault.clar:150) in a state where it satisfy the freshness gate with a timestamp the gate was never meant to accept? Given that it binds a principal to a fresh numeric id, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:150` -> `create`
- Entrypoint: `collateral-remove-redeem` (`mainnet/contracts/market/v0-4-market.clar:1211`), unprivileged and publicly callable
- Attacker controls: `min-underlying`
- Exploit idea: `create` binds a principal to a fresh numeric id. Reach it through `collateral-remove-redeem` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz `min-underlying` across its boundary values through `collateral-remove-redeem` in simnet and assert `create` never returns a value that breaks the invariant.
