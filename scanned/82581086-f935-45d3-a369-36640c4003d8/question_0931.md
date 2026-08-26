# Q0931: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips updatePool

## Question
In rewards/MasterMagpie.sol, emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Can an unprivileged attacker reach this through `emergencyWithdraw(address _stakingToken)` while the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, and drive `_calLpSupply(_stakingToken)` out of agreement with `IERC20(_stakingToken).balanceOf(masterMagpie)` - breaking the invariant that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips updatePool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Precondition: the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it.
- Invariant to test: rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare; concretely, `_calLpSupply(_stakingToken)` must stay reconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, call `emergencyWithdraw(address _stakingToken)`, and assert `_calLpSupply(_stakingToken)` equals `IERC20(_stakingToken).balanceOf(masterMagpie)` and that no account can withdraw more than it put in.
