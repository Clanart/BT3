# Q0886: BribeRewardPool.withdrawFor - queued backlog while totalSupply is zero

## Question
rewards/BribeRewardPool.sol - _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Can an unprivileged attacker controlling the negative delta and whether the claim leg runs, under the attacker votes and casts inside one transaction through voteAndCast, exploit this through `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` to break the reconciliation between `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` and the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: the attacker votes and casts inside one transaction through voteAndCast.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker votes and casts inside one transaction through voteAndCast, then assert `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` end identical in both runs.
