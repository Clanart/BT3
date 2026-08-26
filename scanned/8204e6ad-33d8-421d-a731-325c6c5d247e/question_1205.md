# Q1205: MasterMagpie.massUpdatePools - massUpdatePools reachable by anyone while paused state flips

## Question
Note that in rewards/MasterMagpie.sol, massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Can an attacker holding only tokens bought on market reach it via `massUpdatePools()` under the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it and force `IBaseRewardPool(rewarder).balanceOf(user)` apart from `IBaseRewardPool(rewarder).totalStaked()`, breaking the invariant that no external actor may choose the accrual checkpoints that price other users' deposits for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `massUpdatePools()` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `massUpdatePools()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the block in which every registered pool is rolled forward at once
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, have the attacker run `massUpdatePools()`, then assert the victim's claimable value and the `IBaseRewardPool(rewarder).balanceOf(user)` versus `IBaseRewardPool(rewarder).totalStaked()` relation are unchanged by the attacker's transaction.
