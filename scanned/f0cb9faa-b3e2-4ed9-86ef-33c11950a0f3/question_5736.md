# Q5736: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips the base rewarder

## Question
rewards/MasterMagpie.sol - emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Can an unprivileged attacker controlling _stakingToken and the exact block in which the pool is paused, under the contract is paused so only emergencyWithdraw is reachable, exploit this through `emergencyWithdraw(address _stakingToken)` to break the reconciliation between `userInfo[_stakingToken][user].rewardDebt` and `tokenToPoolInfo[_stakingToken].accMGPPerShare` and the invariant that any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips the base rewarder)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Precondition: the contract is paused so only emergencyWithdraw is reachable.
- Invariant to test: any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance; concretely, `userInfo[_stakingToken][user].rewardDebt` must stay reconciled with `tokenToPoolInfo[_stakingToken].accMGPPerShare`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the contract is paused so only emergencyWithdraw is reachable, have the attacker run `emergencyWithdraw(address _stakingToken)`, then assert the victim's claimable value and the `userInfo[_stakingToken][user].rewardDebt` versus `tokenToPoolInfo[_stakingToken].accMGPPerShare` relation are unchanged by the attacker's transaction.
