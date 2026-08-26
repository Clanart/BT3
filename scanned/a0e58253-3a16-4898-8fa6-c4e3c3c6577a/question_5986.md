# Q5986: MasterMagpie.updatePool - massUpdatePools reachable by anyone while paused state flips

## Question
Consider rewards/MasterMagpie.sol, where massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Assuming the attacker repeats the call in the same block to observe the second, no-op iteration, can an unprivileged attacker turn this into a divergence between `totalAllocPoint` and `tokenToPoolInfo[_stakingToken].allocPoint` via `updatePool(address _stakingToken)`, breaking the invariant that no external actor may choose the accrual checkpoints that price other users' deposits and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the attacker repeats the call in the same block to observe the second, no-op iteration.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker repeats the call in the same block to observe the second, no-op iteration, then assert `totalAllocPoint` and `tokenToPoolInfo[_stakingToken].allocPoint` end identical in both runs.
