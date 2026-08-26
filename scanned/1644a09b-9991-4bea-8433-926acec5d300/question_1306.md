# Q1306: BribeRewardPool.stakeFor - queued backlog while totalSupply is zero

## Question
rewards/BribeRewardPool.sol: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Under totalSupply is zero because every voter has unvoted, is there an unprivileged sequence of `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` that leaves `userRewards[_rewardToken][account]` unreconciled with `earned(account,_rewardToken)`, violates the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: totalSupply is zero because every voter has unvoted.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` sequence atomically under totalSupply is zero because every voter has unvoted, asserting at the end that `userRewards[_rewardToken][account]` still equals `earned(account,_rewardToken)` and the PoC's balance delta is non-positive.
