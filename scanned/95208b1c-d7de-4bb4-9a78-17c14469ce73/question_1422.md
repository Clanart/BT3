# Q1422: relevant via supply-collateral-add: satisfy the freshness gate with a timestamp the gate was n

## Question
Entering through `supply-collateral-add` (mainnet/contracts/market/v0-4-market.clar:1175) while controlling the `ft` trait principal deciding which vault is routed to, can an unprivileged attacker make `relevant` (mainnet/contracts/market/v0-market-vault.clar:175) satisfy the freshness gate with a timestamp the gate was never meant to accept? `relevant` drops any position row whose bit is not present in the enabled mask, so the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call would fail, yielding permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:175` -> `relevant`
- Entrypoint: `supply-collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1175`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal deciding which vault is routed to
- Exploit idea: `relevant` drops any position row whose bit is not present in the enabled mask. Reach it through `supply-collateral-add` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz the `ft` trait principal deciding which vault is routed to across its boundary values through `supply-collateral-add` in simnet and assert `relevant` never returns a value that breaks the invariant.
