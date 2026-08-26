# Q0209: MGPRelease.claim - the revoked flag is checked only at the top of claim

## Question
Note that in rewards/MGPRelease.sol, claim() reverts on vesting.revoked but getClaimable ignores the flag entirely, so any integrator reading the accessor sees an entitlement the claim path will refuse. Can an attacker holding only tokens bought on market reach it via `claim()` under block.timestamp is below startTimestamp and the initial tranche has already been claimed and force `initialUnlockedAmount` apart from `beneficiaries[account].claimed`, breaking the invariant that the accessor and the settlement path must agree on whether an entitlement exists for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the revoked flag is checked only at the top of claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() reverts on vesting.revoked but getClaimable ignores the flag entirely, so any integrator reading the accessor sees an entitlement the claim path will refuse. Precondition: block.timestamp is below startTimestamp and the initial tranche has already been claimed.
- Invariant to test: the accessor and the settlement path must agree on whether an entitlement exists; concretely, `initialUnlockedAmount` must stay reconciled with `beneficiaries[account].claimed`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `claim()` sequence atomically under block.timestamp is below startTimestamp and the initial tranche has already been claimed, asserting at the end that `initialUnlockedAmount` still equals `beneficiaries[account].claimed` and the PoC's balance delta is non-positive.
