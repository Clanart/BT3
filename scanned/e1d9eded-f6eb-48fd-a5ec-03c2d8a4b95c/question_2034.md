# Q2034: add-user-collateral via liquidate-redeem: satisfy the freshness gate with a timestamp the gate was n

## Question
Entering through `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604) while controlling the borrower targeted, can an unprivileged attacker make `add-user-collateral` (mainnet/contracts/market/v0-market-vault.clar:198) satisfy the freshness gate with a timestamp the gate was never meant to accept? `add-user-collateral` adds to the collateral row with a graceful u0 default, so the invariant that a position that holds value can always be priced, and therefore always closed would fail, yielding permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:198` -> `add-user-collateral`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the borrower targeted
- Exploit idea: `add-user-collateral` adds to the collateral row with a graceful u0 default. Reach it through `liquidate-redeem` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz the borrower targeted across its boundary values through `liquidate-redeem` in simnet and assert `add-user-collateral` never returns a value that breaks the invariant.
