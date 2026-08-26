# Q0612: MGPRelease.claim - the schedule boundaries are read live on every claim

## Question
In rewards/MGPRelease.sol, getClaimable() branches on block.timestamp against startTimestamp and endTimestamp on every call, so any change to those boundaries retroactively rewrites the vested figure against an already-advanced claimed counter. Can an unprivileged attacker reach this through `claim()` while block.timestamp is exactly endTimestamp, and drive `vested` out of agreement with `beneficiaries[account].totalAlloced - initialUnlockedAmount` - breaking the invariant that a vesting schedule must not be able to move under a beneficiary who has already claimed against it - for Critical - Permanent freezing of funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the schedule boundaries are read live on every claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: getClaimable() branches on block.timestamp against startTimestamp and endTimestamp on every call, so any change to those boundaries retroactively rewrites the vested figure against an already-advanced claimed counter. Precondition: block.timestamp is exactly endTimestamp.
- Invariant to test: a vesting schedule must not be able to move under a beneficiary who has already claimed against it; concretely, `vested` must stay reconciled with `beneficiaries[account].totalAlloced - initialUnlockedAmount`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `claim()`: constrain the setup so that block.timestamp is exactly endTimestamp, fuzz the attacker inputs (the exact block at which the linear release is evaluated, and how often it is repeated), and assert after every call that a vesting schedule must not be able to move under a beneficiary who has already claimed against it.
