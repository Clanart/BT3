# Q2838: resolve via borrow: make a required price path abort so the position can no lo

## Question
Entering through `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) while controlling `receiver`, including a contract principal, can an unprivileged attacker make `resolve` (mainnet/contracts/registry/v0-egroup.clar:360) make a required price path abort so the position can no longer be closed or seized? `resolve` selects the efficiency group for a position mask, so the invariant that a position that holds value can always be priced, and therefore always closed would fail, yielding permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/registry/v0-egroup.clar:360` -> `resolve`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: `receiver`, including a contract principal
- Exploit idea: `resolve` selects the efficiency group for a position mask. Reach it through `borrow` and make a required price path abort so the position can no longer be closed or seized.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `receiver`, including a contract principal across its boundary values through `borrow` in simnet and assert `resolve` never returns a value that breaks the invariant.
