# Q0829: MGPRelease.claim - the schedule boundaries are read live on every claim

## Question
rewards/MGPRelease.sol: getClaimable() branches on block.timestamp against startTimestamp and endTimestamp on every call, so any change to those boundaries retroactively rewrites the vested figure against an already-advanced claimed counter. With the exact block at which the linear release is evaluated, and how often it is repeated under attacker control and the beneficiary claims repeatedly inside one block, can an unprivileged caller sequence `claim()` so that `sum of all totalAlloced` and `IERC20(tokenToRelease).balanceOf(address(this))` no longer reconcile, violating the invariant that a vesting schedule must not be able to move under a beneficiary who has already claimed against it and realising Critical - Permanent freezing of funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the schedule boundaries are read live on every claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: getClaimable() branches on block.timestamp against startTimestamp and endTimestamp on every call, so any change to those boundaries retroactively rewrites the vested figure against an already-advanced claimed counter. Precondition: the beneficiary claims repeatedly inside one block.
- Invariant to test: a vesting schedule must not be able to move under a beneficiary who has already claimed against it; concretely, `sum of all totalAlloced` must stay reconciled with `IERC20(tokenToRelease).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the beneficiary claims repeatedly inside one block, then assert `sum of all totalAlloced` and `IERC20(tokenToRelease).balanceOf(address(this))` end identical in both runs.
