# Q2014: relevant via liquidate-multi: attach a price resolved for one asset to a different asset

## Question
Entering through `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593) while controlling the trait principals supplied per entry, can an unprivileged attacker make `relevant` (mainnet/contracts/market/v0-market-vault.clar:175) attach a price resolved for one asset to a different asset in the position? `relevant` drops any position row whose bit is not present in the enabled mask, so the invariant that a position that holds value can always be priced, and therefore always closed would fail, yielding direct theft of another user's collateral.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:175` -> `relevant`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: the trait principals supplied per entry
- Exploit idea: `relevant` drops any position row whose bit is not present in the enabled mask. Reach it through `liquidate-multi` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: Set up the position in simnet, call `liquidate-multi` with the trait principals supplied per entry, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
