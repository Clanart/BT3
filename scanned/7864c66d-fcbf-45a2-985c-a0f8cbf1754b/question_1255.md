# Q1255: MGPRelease.claim - the schedule boundaries are read live on every claim

## Question
In rewards/MGPRelease.sol, getClaimable() branches on block.timestamp against startTimestamp and endTimestamp on every call, so any change to those boundaries retroactively rewrites the vested figure against an already-advanced claimed counter. Starting from a state where initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation, can an unprivileged EOA use `claim()` to leave `beneficiaries[account].claimed` inconsistent with `getClaimable(account)`, violating the invariant that a vesting schedule must not be able to move under a beneficiary who has already claimed against it and extracting Critical - Permanent freezing of funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the schedule boundaries are read live on every claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: getClaimable() branches on block.timestamp against startTimestamp and endTimestamp on every call, so any change to those boundaries retroactively rewrites the vested figure against an already-advanced claimed counter. Precondition: initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation.
- Invariant to test: a vesting schedule must not be able to move under a beneficiary who has already claimed against it; concretely, `beneficiaries[account].claimed` must stay reconciled with `getClaimable(account)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `claim()` sequence atomically under initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation, asserting at the end that `beneficiaries[account].claimed` still equals `getClaimable(account)` and the PoC's balance delta is non-positive.
