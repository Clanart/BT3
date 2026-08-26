# Q0643: MGPRelease.claim - the revoked flag is checked only at the top of claim

## Question
In rewards/MGPRelease.sol, claim() reverts on vesting.revoked but getClaimable ignores the flag entirely, so any integrator reading the accessor sees an entitlement the claim path will refuse. Can an unprivileged attacker reach this through `claim()` while block.timestamp is exactly endTimestamp, and drive `sum of all totalAlloced` out of agreement with `IERC20(tokenToRelease).balanceOf(address(this))` - breaking the invariant that the accessor and the settlement path must agree on whether an entitlement exists - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the revoked flag is checked only at the top of claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() reverts on vesting.revoked but getClaimable ignores the flag entirely, so any integrator reading the accessor sees an entitlement the claim path will refuse. Precondition: block.timestamp is exactly endTimestamp.
- Invariant to test: the accessor and the settlement path must agree on whether an entitlement exists; concretely, `sum of all totalAlloced` must stay reconciled with `IERC20(tokenToRelease).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the linear release is evaluated, and how often it is repeated) under block.timestamp is exactly endTimestamp, asserting on every row that the accessor and the settlement path must agree on whether an entitlement exists.
