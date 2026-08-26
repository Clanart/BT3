# Q3537: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips the base rewarder

## Question
In rewards/MasterMagpie.sol, emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Does `emergencyWithdraw(address _stakingToken)` let an unprivileged caller exploit that under a large honest deposit is sitting in the mempool and the attacker sandwiches it, so that `IBaseRewardPool(rewarder).balanceOf(user)` diverges from `IBaseRewardPool(rewarder).totalStaked()`, the invariant that any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips the base rewarder)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Precondition: a large honest deposit is sitting in the mempool and the attacker sandwiches it.
- Invariant to test: any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a large honest deposit is sitting in the mempool and the attacker sandwiches it, have the attacker run `emergencyWithdraw(address _stakingToken)`, then assert the victim's claimable value and the `IBaseRewardPool(rewarder).balanceOf(user)` versus `IBaseRewardPool(rewarder).totalStaked()` relation are unchanged by the attacker's transaction.
