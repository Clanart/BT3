# Q5617: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips the base rewarder

## Question
In rewards/MasterMagpie.sol, emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Does `emergencyWithdraw(address _stakingToken)` let an unprivileged caller exploit that under the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, so that `userInfo[_stakingToken][user].available` diverges from `userInfo[_stakingToken][user].amount`, the invariant that any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips the base rewarder)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Precondition: the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp.
- Invariant to test: any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance; concretely, `userInfo[_stakingToken][user].available` must stay reconciled with `userInfo[_stakingToken][user].amount`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `emergencyWithdraw(address _stakingToken)` sequence atomically under the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, asserting at the end that `userInfo[_stakingToken][user].available` still equals `userInfo[_stakingToken][user].amount` and the PoC's balance delta is non-positive.
