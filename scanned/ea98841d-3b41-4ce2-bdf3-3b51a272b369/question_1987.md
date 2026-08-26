# Q1987: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips updatePool

## Question
In rewards/MasterMagpie.sol, emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Can an unprivileged attacker reach this through `emergencyWithdraw(address _stakingToken)` while the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, and drive `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` out of agreement with `block.timestamp` - breaking the invariant that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips updatePool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Precondition: the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake.
- Invariant to test: rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, call `emergencyWithdraw(address _stakingToken)`, and assert `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` equals `block.timestamp` and that no account can withdraw more than it put in.
