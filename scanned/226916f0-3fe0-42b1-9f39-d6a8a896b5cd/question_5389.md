# Q5389: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips the base rewarder

## Question
rewards/MasterMagpie.sol: emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. With _stakingToken and the exact block in which the pool is paused under attacker control and the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), can an unprivileged caller sequence `emergencyWithdraw(address _stakingToken)` so that `userInfo[_stakingToken][user].amount` and `_calLpSupply(_stakingToken)` no longer reconcile, violating the invariant that any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips the base rewarder)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked().
- Invariant to test: any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance; concretely, `userInfo[_stakingToken][user].amount` must stay reconciled with `_calLpSupply(_stakingToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_stakingToken and the exact block in which the pool is paused) under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), asserting on every row that any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance.
