# Q1046: MGPRelease.claim - the schedule boundaries are read live on every claim

## Question
Consider rewards/MGPRelease.sol, where getClaimable() branches on block.timestamp against startTimestamp and endTimestamp on every call, so any change to those boundaries retroactively rewrites the vested figure against an already-advanced claimed counter. Assuming the contract balance is below the sum of unclaimed allocations, can an unprivileged attacker turn this into a divergence between `startTimestamp and endTimestamp` and `block.timestamp` via `claim()`, breaking the invariant that a vesting schedule must not be able to move under a beneficiary who has already claimed against it and producing Critical - Permanent freezing of funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the schedule boundaries are read live on every claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: getClaimable() branches on block.timestamp against startTimestamp and endTimestamp on every call, so any change to those boundaries retroactively rewrites the vested figure against an already-advanced claimed counter. Precondition: the contract balance is below the sum of unclaimed allocations.
- Invariant to test: a vesting schedule must not be able to move under a beneficiary who has already claimed against it; concretely, `startTimestamp and endTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `claim()` sequence atomically under the contract balance is below the sum of unclaimed allocations, asserting at the end that `startTimestamp and endTimestamp` still equals `block.timestamp` and the PoC's balance delta is non-positive.
