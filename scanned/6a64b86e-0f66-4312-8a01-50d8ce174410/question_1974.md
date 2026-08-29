# Q1974: resolve-dia via collateral-remove: produce a price that passes `oracle-price-legal` while bei

## Question
Entering through `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) while controlling `receiver`, including a contract principal, can an unprivileged attacker make `resolve-dia` (mainnet/contracts/market/v0-4-market.clar:326) produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? `resolve-dia` derives a (string-ascii 32) key from a (buff 32) ident, so the invariant that a position that holds value can always be priced, and therefore always closed would fail, yielding permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:326` -> `resolve-dia`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: `receiver`, including a contract principal
- Exploit idea: `resolve-dia` derives a (string-ascii 32) key from a (buff 32) ident. Reach it through `collateral-remove` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `receiver`, including a contract principal across its boundary values through `collateral-remove` in simnet and assert `resolve-dia` never returns a value that breaks the invariant.
