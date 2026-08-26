# Q5836: MasterMagpie.massUpdatePools - massUpdatePools reachable by anyone while paused state flips

## Question
In rewards/MasterMagpie.sol, massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Does `massUpdatePools()` let an unprivileged caller exploit that under the victim has a large unClaimedMgp balance that has not been settled for several epochs, so that `IBaseRewardPool(rewarder).balanceOf(user)` diverges from `IBaseRewardPool(rewarder).totalStaked()`, the invariant that no external actor may choose the accrual checkpoints that price other users' deposits is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `massUpdatePools()` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `massUpdatePools()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the block in which every registered pool is rolled forward at once
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the victim has a large unClaimedMgp balance that has not been settled for several epochs.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the victim has a large unClaimedMgp balance that has not been settled for several epochs, have the attacker run `massUpdatePools()`, then assert the victim's claimable value and the `IBaseRewardPool(rewarder).balanceOf(user)` versus `IBaseRewardPool(rewarder).totalStaked()` relation are unchanged by the attacker's transaction.
