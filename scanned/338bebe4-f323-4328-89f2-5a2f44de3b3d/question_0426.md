# Q0426: MGPRelease.claim - the revoked flag is checked only at the top of claim

## Question
rewards/MGPRelease.sol: claim() reverts on vesting.revoked but getClaimable ignores the flag entirely, so any integrator reading the accessor sees an entitlement the claim path will refuse. Under block.timestamp is exactly startTimestamp, is there an unprivileged sequence of `claim()` that leaves `vested` unreconciled with `beneficiaries[account].totalAlloced - initialUnlockedAmount`, violates the invariant that the accessor and the settlement path must agree on whether an entitlement exists, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the revoked flag is checked only at the top of claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() reverts on vesting.revoked but getClaimable ignores the flag entirely, so any integrator reading the accessor sees an entitlement the claim path will refuse. Precondition: block.timestamp is exactly startTimestamp.
- Invariant to test: the accessor and the settlement path must agree on whether an entitlement exists; concretely, `vested` must stay reconciled with `beneficiaries[account].totalAlloced - initialUnlockedAmount`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish block.timestamp is exactly startTimestamp, have the attacker run `claim()`, then assert the victim's claimable value and the `vested` versus `beneficiaries[account].totalAlloced - initialUnlockedAmount` relation are unchanged by the attacker's transaction.
