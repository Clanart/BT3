# Q0516: oracle-price-legal via collateral-add: satisfy the freshness gate with a timestamp the gate was n

## Question
Does `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) let an unprivileged attacker who controls call ordering within the block reach `oracle-price-legal` (mainnet/contracts/market/v0-4-market.clar:362) in a state where it satisfy the freshness gate with a timestamp the gate was never meant to accept? Given that it accepts any price strictly greater than zero, with no upper bound and no sanity band, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:362` -> `oracle-price-legal`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: call ordering within the block
- Exploit idea: `oracle-price-legal` accepts any price strictly greater than zero, with no upper bound and no sanity band. Reach it through `collateral-add` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz call ordering within the block across its boundary values through `collateral-add` in simnet and assert `oracle-price-legal` never returns a value that breaks the invariant.
