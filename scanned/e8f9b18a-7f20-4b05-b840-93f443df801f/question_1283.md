# Q1283: MGPRelease.claim - the revoked flag is checked only at the top of claim

## Question
In rewards/MGPRelease.sol, claim() reverts on vesting.revoked but getClaimable ignores the flag entirely, so any integrator reading the accessor sees an entitlement the claim path will refuse. Can an unprivileged attacker reach this through `claim()` while initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation, and drive `initialUnlockedAmount` out of agreement with `beneficiaries[account].claimed` - breaking the invariant that the accessor and the settlement path must agree on whether an entitlement exists - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the revoked flag is checked only at the top of claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() reverts on vesting.revoked but getClaimable ignores the flag entirely, so any integrator reading the accessor sees an entitlement the claim path will refuse. Precondition: initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation.
- Invariant to test: the accessor and the settlement path must agree on whether an entitlement exists; concretely, `initialUnlockedAmount` must stay reconciled with `beneficiaries[account].claimed`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation, have the attacker run `claim()`, then assert the victim's claimable value and the `initialUnlockedAmount` versus `beneficiaries[account].claimed` relation are unchanged by the attacker's transaction.
