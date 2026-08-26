# Q5818: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips updatePool

## Question
rewards/MasterMagpie.sol: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Under the victim has a large unClaimedMgp balance that has not been settled for several epochs, is there an unprivileged sequence of `emergencyWithdraw(address _stakingToken)` that leaves `_calLpSupply(_stakingToken)` unreconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`, violates the invariant that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips updatePool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Precondition: the victim has a large unClaimedMgp balance that has not been settled for several epochs.
- Invariant to test: rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare; concretely, `_calLpSupply(_stakingToken)` must stay reconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the victim has a large unClaimedMgp balance that has not been settled for several epochs, have the attacker run `emergencyWithdraw(address _stakingToken)`, then assert the victim's claimable value and the `_calLpSupply(_stakingToken)` versus `IERC20(_stakingToken).balanceOf(masterMagpie)` relation are unchanged by the attacker's transaction.
