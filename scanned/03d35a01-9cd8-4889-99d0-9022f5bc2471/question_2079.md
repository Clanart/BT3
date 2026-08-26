# Q2079: MasterMagpie.updatePool - emergencyWithdraw skips updatePool

## Question
In rewards/MasterMagpie.sol, emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Does `updatePool(address _stakingToken)` let an unprivileged caller exploit that under the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, so that `IBaseRewardPool(rewarder).balanceOf(user)` diverges from `IBaseRewardPool(rewarder).totalStaked()`, the invariant that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: emergencyWithdraw skips updatePool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Precondition: the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake.
- Invariant to test: rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updatePool(address _stakingToken)`: constrain the setup so that the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, fuzz the attacker inputs (_stakingToken and the timestamp at which accMGPPerShare is rolled forward), and assert after every call that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare.
