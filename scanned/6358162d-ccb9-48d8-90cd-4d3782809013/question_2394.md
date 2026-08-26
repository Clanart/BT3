# Q2394: BribeRewardPool.withdrawFor - _getReward recomputes earned after the modifier already synced

## Question
In rewards/BribeRewardPool.sol, withdrawFor() carries updateRewards(_for, rewardTokens) and then _getReward() calls earned() again, so the settlement depends on the two reads agreeing across the intervening state writes. Does `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` let an unprivileged caller exploit that under the bribe token has begun reverting on transfer, so that `userRewards[_rewardToken][account]` diverges from `earned(account,_rewardToken)`, the invariant that a settlement amount must be read once from a single synchronised source is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: _getReward recomputes earned after the modifier already synced)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: withdrawFor() carries updateRewards(_for, rewardTokens) and then _getReward() calls earned() again, so the settlement depends on the two reads agreeing across the intervening state writes. Precondition: the bribe token has begun reverting on transfer.
- Invariant to test: a settlement amount must be read once from a single synchronised source; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the negative delta and whether the claim leg runs) under the bribe token has begun reverting on transfer, asserting on every row that a settlement amount must be read once from a single synchronised source.
