# Q3553: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips updatePool

## Question
Note that in rewards/MasterMagpie.sol, emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Can an attacker holding only tokens bought on market reach it via `emergencyWithdraw(address _stakingToken)` under a large honest deposit is sitting in the mempool and the attacker sandwiches it and force `totalAllocPoint` apart from `tokenToPoolInfo[_stakingToken].allocPoint`, breaking the invariant that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips updatePool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Precondition: a large honest deposit is sitting in the mempool and the attacker sandwiches it.
- Invariant to test: rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large honest deposit is sitting in the mempool and the attacker sandwiches it, call `emergencyWithdraw(address _stakingToken)`, and assert `totalAllocPoint` equals `tokenToPoolInfo[_stakingToken].allocPoint` and that no account can withdraw more than it put in.
