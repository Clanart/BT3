# Q5396: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips updatePool

## Question
rewards/MasterMagpie.sol: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. With _stakingToken and the exact block in which the pool is paused under attacker control and the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), can an unprivileged caller sequence `emergencyWithdraw(address _stakingToken)` so that `userInfo[_stakingToken][user].available` and `userInfo[_stakingToken][user].amount` no longer reconcile, violating the invariant that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips updatePool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked().
- Invariant to test: rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare; concretely, `userInfo[_stakingToken][user].available` must stay reconciled with `userInfo[_stakingToken][user].amount`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), snapshot `userInfo[_stakingToken][user].available` and `userInfo[_stakingToken][user].amount`, run the attacker's `emergencyWithdraw(address _stakingToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
