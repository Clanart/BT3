# Q2436: calc-index-next via supply-collateral-add: produce a price that passes `oracle-price-legal` while bei

## Question
Does `supply-collateral-add` (mainnet/contracts/market/v0-4-market.clar:1175) let an unprivileged attacker who controls the `ft` trait principal deciding which vault is routed to reach `calc-index-next` (mainnet/contracts/vault/v0-vault-stx.clar:183) in a state where it produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? Given that it applies a multiplier to the current index, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:183` -> `calc-index-next`
- Entrypoint: `supply-collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1175`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal deciding which vault is routed to
- Exploit idea: `calc-index-next` applies a multiplier to the current index. Reach it through `supply-collateral-add` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz the `ft` trait principal deciding which vault is routed to across its boundary values through `supply-collateral-add` in simnet and assert `calc-index-next` never returns a value that breaks the invariant.
