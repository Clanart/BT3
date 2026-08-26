# Q5896: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips the base rewarder

## Question
rewards/MasterMagpie.sol - emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Can an unprivileged attacker controlling _stakingToken and the exact block in which the pool is paused, under the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, exploit this through `emergencyWithdraw(address _stakingToken)` to break the reconciliation between `_calLpSupply(_stakingToken)` and `IERC20(_stakingToken).balanceOf(masterMagpie)` and the invariant that any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips the base rewarder)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Precondition: the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18.
- Invariant to test: any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance; concretely, `_calLpSupply(_stakingToken)` must stay reconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_stakingToken and the exact block in which the pool is paused) under the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, asserting on every row that any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance.
