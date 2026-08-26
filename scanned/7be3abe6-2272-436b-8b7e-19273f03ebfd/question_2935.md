# Q2935: MasterMagpie.updatePool - emergencyWithdraw skips updatePool

## Question
In rewards/MasterMagpie.sol, emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Does `updatePool(address _stakingToken)` let an unprivileged caller exploit that under the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, so that `totalAllocPoint` diverges from `tokenToPoolInfo[_stakingToken].allocPoint`, the invariant that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: emergencyWithdraw skips updatePool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Precondition: the attacker holds one wei of stake so lpSupply is non-zero but every division truncates.
- Invariant to test: rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, have the attacker run `updatePool(address _stakingToken)`, then assert the victim's claimable value and the `totalAllocPoint` versus `tokenToPoolInfo[_stakingToken].allocPoint` relation are unchanged by the attacker's transaction.
