# Q0250: zip via call-ststx-ratio: satisfy the freshness gate with a timestamp the gate was n

## Question
Entering through `call-ststx-ratio` (mainnet/contracts/market/v0-4-market.clar:1015) while controlling whether the ratio is fetched before or after other state changes in the block, can an unprivileged attacker make `zip` (mainnet/contracts/vault/v0-vault-stx.clar:226) satisfy the freshness gate with a timestamp the gate was never meant to accept? `zip` pairs the utilization and rate point lists element by element, so the invariant that collateral is valued low and debt is valued high at every call site without exception would fail, yielding theft of unclaimed yield.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:226` -> `zip`
- Entrypoint: `call-ststx-ratio` (`mainnet/contracts/market/v0-4-market.clar:1015`), unprivileged and publicly callable
- Attacker controls: whether the ratio is fetched before or after other state changes in the block
- Exploit idea: `zip` pairs the utilization and rate point lists element by element. Reach it through `call-ststx-ratio` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: High - theft of unclaimed yield
- Fast validation: Set up the position in simnet, call `call-ststx-ratio` with whether the ratio is fetched before or after other state changes in the block, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
