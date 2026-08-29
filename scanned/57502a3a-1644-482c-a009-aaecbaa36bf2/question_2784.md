# Q2784: unwrap-status via borrow: attach a price resolved for one asset to a different asset

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls `receiver`, including a contract principal reach `unwrap-status` (mainnet/contracts/registry/v0-assets.clar:111) in a state where it attach a price resolved for one asset to a different asset in the position? Given that it resolves `status` with `unwrap-panic`, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/registry/v0-assets.clar:111` -> `unwrap-status`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: `receiver`, including a contract principal
- Exploit idea: `unwrap-status` resolves `status` with `unwrap-panic`. Reach it through `borrow` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `receiver`, including a contract principal across its boundary values through `borrow` in simnet and assert `unwrap-status` never returns a value that breaks the invariant.
