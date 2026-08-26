# Q1964: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips the base rewarder

## Question
In rewards/MasterMagpie.sol, emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Can an unprivileged attacker reach this through `emergencyWithdraw(address _stakingToken)` while the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, and drive `_calLpSupply(_stakingToken)` out of agreement with `IERC20(_stakingToken).balanceOf(masterMagpie)` - breaking the invariant that any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips the base rewarder)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Precondition: the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake.
- Invariant to test: any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance; concretely, `_calLpSupply(_stakingToken)` must stay reconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, have the attacker run `emergencyWithdraw(address _stakingToken)`, then assert the victim's claimable value and the `_calLpSupply(_stakingToken)` versus `IERC20(_stakingToken).balanceOf(masterMagpie)` relation are unchanged by the attacker's transaction.
