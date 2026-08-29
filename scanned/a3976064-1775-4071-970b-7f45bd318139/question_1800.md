# Q1800: resolve-or-create via liquidate: satisfy the freshness gate with a timestamp the gate was n

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `borrower`, any third-party principal reach `resolve-or-create` (mainnet/contracts/market/v0-market-vault.clar:143) in a state where it satisfy the freshness gate with a timestamp the gate was never meant to accept? Given that it allocates a user id through `increment` for whatever principal the market names, the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:143` -> `resolve-or-create`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `borrower`, any third-party principal
- Exploit idea: `resolve-or-create` allocates a user id through `increment` for whatever principal the market names. Reach it through `liquidate` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz `borrower`, any third-party principal across its boundary values through `liquidate` in simnet and assert `resolve-or-create` never returns a value that breaks the invariant.
