# Q5916: MasterMagpie.massUpdatePools - massUpdatePools reachable by anyone while paused state flips

## Question
In rewards/MasterMagpie.sol, massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Does `massUpdatePools()` let an unprivileged caller exploit that under the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, so that `totalAllocPoint` diverges from `tokenToPoolInfo[_stakingToken].allocPoint`, the invariant that no external actor may choose the accrual checkpoints that price other users' deposits is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `massUpdatePools()` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `massUpdatePools()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the block in which every registered pool is rolled forward at once
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `massUpdatePools()`: constrain the setup so that the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, fuzz the attacker inputs (the block in which every registered pool is rolled forward at once), and assert after every call that no external actor may choose the accrual checkpoints that price other users' deposits.
