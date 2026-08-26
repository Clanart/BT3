# Q2840: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips the base rewarder

## Question
rewards/MasterMagpie.sol - emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Can an unprivileged attacker controlling _stakingToken and the exact block in which the pool is paused, under the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, exploit this through `emergencyWithdraw(address _stakingToken)` to break the reconciliation between `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` and `block.timestamp` and the invariant that any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips the base rewarder)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Precondition: the attacker holds one wei of stake so lpSupply is non-zero but every division truncates.
- Invariant to test: any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, snapshot `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` and `block.timestamp`, run the attacker's `emergencyWithdraw(address _stakingToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
