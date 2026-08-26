# Q5738: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips updatePool

## Question
rewards/MasterMagpie.sol: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Under the contract is paused so only emergencyWithdraw is reachable, is there an unprivileged sequence of `emergencyWithdraw(address _stakingToken)` that leaves `unClaimedMgp[_stakingToken][user]` unreconciled with `userInfo[_stakingToken][user].rewardDebt`, violates the invariant that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips updatePool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Precondition: the contract is paused so only emergencyWithdraw is reachable.
- Invariant to test: rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare; concretely, `unClaimedMgp[_stakingToken][user]` must stay reconciled with `userInfo[_stakingToken][user].rewardDebt`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract is paused so only emergencyWithdraw is reachable, call `emergencyWithdraw(address _stakingToken)`, and assert `unClaimedMgp[_stakingToken][user]` equals `userInfo[_stakingToken][user].rewardDebt` and that no account can withdraw more than it put in.
