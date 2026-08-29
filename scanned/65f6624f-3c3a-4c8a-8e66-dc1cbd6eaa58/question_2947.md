# Q2947: socialize-debt-asset via liquidate-redeem: satisfy the freshness gate with a timestamp the gate was n

## Question
`socialize-debt-asset` (mainnet/contracts/market/v0-4-market.clar:879) calls the vault write-down, then overwrites `index-cache` for the current timestamp mid-fold, and carries a `success` flag that short-circuits. Can an unprivileged caller of `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604), by choosing the redemption receiver, use that to satisfy the freshness gate with a timestamp the gate was never meant to accept, violating the invariant that collateral is valued low and debt is valued high at every call site without exception and producing direct theft of another user's collateral?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:879` -> `socialize-debt-asset`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the redemption receiver
- Exploit idea: `socialize-debt-asset` calls the vault write-down, then overwrites `index-cache` for the current timestamp mid-fold, and carries a `success` flag that short-circuits. Reach it through `liquidate-redeem` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: In `local-testing/tests` on a local fork, drive `liquidate-redeem` with the redemption receiver, then read `socialize-debt-asset` state before and after in the same block and assert the two sides of the invariant are equal.
