# Q0900: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips the base rewarder

## Question
In rewards/MasterMagpie.sol, emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Can an unprivileged attacker reach this through `emergencyWithdraw(address _stakingToken)` while the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, and drive `unClaimedMgp[_stakingToken][user]` out of agreement with `userInfo[_stakingToken][user].rewardDebt` - breaking the invariant that any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips the base rewarder)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Precondition: the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it.
- Invariant to test: any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance; concretely, `unClaimedMgp[_stakingToken][user]` must stay reconciled with `userInfo[_stakingToken][user].rewardDebt`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `emergencyWithdraw(address _stakingToken)` sequence atomically under the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, asserting at the end that `unClaimedMgp[_stakingToken][user]` still equals `userInfo[_stakingToken][user].rewardDebt` and the PoC's balance delta is non-positive.
