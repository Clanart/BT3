# Q5816: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips the base rewarder

## Question
rewards/MasterMagpie.sol - emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Can an unprivileged attacker controlling _stakingToken and the exact block in which the pool is paused, under the victim has a large unClaimedMgp balance that has not been settled for several epochs, exploit this through `emergencyWithdraw(address _stakingToken)` to break the reconciliation between `unClaimedMgp[_stakingToken][user]` and `userInfo[_stakingToken][user].rewardDebt` and the invariant that any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips the base rewarder)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Precondition: the victim has a large unClaimedMgp balance that has not been settled for several epochs.
- Invariant to test: any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance; concretely, `unClaimedMgp[_stakingToken][user]` must stay reconciled with `userInfo[_stakingToken][user].rewardDebt`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `emergencyWithdraw(address _stakingToken)`: constrain the setup so that the victim has a large unClaimedMgp balance that has not been settled for several epochs, fuzz the attacker inputs (_stakingToken and the exact block in which the pool is paused), and assert after every call that any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance.
