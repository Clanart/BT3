# Q5456: MasterMagpie.massUpdatePools - massUpdatePools reachable by anyone while paused state flips

## Question
rewards/MasterMagpie.sol: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. With the block in which every registered pool is rolled forward at once under attacker control and the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), can an unprivileged caller sequence `massUpdatePools()` so that `unClaimedMgp[_stakingToken][user]` and `userInfo[_stakingToken][user].rewardDebt` no longer reconcile, violating the invariant that no external actor may choose the accrual checkpoints that price other users' deposits and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `massUpdatePools()` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `massUpdatePools()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the block in which every registered pool is rolled forward at once
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked().
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `unClaimedMgp[_stakingToken][user]` must stay reconciled with `userInfo[_stakingToken][user].rewardDebt`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), snapshot `unClaimedMgp[_stakingToken][user]` and `userInfo[_stakingToken][user].rewardDebt`, run the attacker's `massUpdatePools()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
