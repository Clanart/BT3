# Q0395: MGPRelease.claim - the schedule boundaries are read live on every claim

## Question
rewards/MGPRelease.sol: getClaimable() branches on block.timestamp against startTimestamp and endTimestamp on every call, so any change to those boundaries retroactively rewrites the vested figure against an already-advanced claimed counter. Under block.timestamp is exactly startTimestamp, is there an unprivileged sequence of `claim()` that leaves `initialUnlockedAmount` unreconciled with `beneficiaries[account].claimed`, violates the invariant that a vesting schedule must not be able to move under a beneficiary who has already claimed against it, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the schedule boundaries are read live on every claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: getClaimable() branches on block.timestamp against startTimestamp and endTimestamp on every call, so any change to those boundaries retroactively rewrites the vested figure against an already-advanced claimed counter. Precondition: block.timestamp is exactly startTimestamp.
- Invariant to test: a vesting schedule must not be able to move under a beneficiary who has already claimed against it; concretely, `initialUnlockedAmount` must stay reconciled with `beneficiaries[account].claimed`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange block.timestamp is exactly startTimestamp, call `claim()`, and assert `initialUnlockedAmount` equals `beneficiaries[account].claimed` and that no account can withdraw more than it put in.
