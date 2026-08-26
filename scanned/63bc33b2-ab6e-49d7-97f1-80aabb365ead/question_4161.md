# Q4161: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips the base rewarder

## Question
In rewards/MasterMagpie.sol, emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Does `emergencyWithdraw(address _stakingToken)` let an unprivileged caller exploit that under the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, so that `totalAllocPoint` diverges from `tokenToPoolInfo[_stakingToken].allocPoint`, the invariant that any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips the base rewarder)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty.
- Invariant to test: any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, snapshot `totalAllocPoint` and `tokenToPoolInfo[_stakingToken].allocPoint`, run the attacker's `emergencyWithdraw(address _stakingToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
