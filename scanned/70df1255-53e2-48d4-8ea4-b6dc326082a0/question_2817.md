# Q2817: BribeRewardPool.withdrawFor - _getReward recomputes earned after the modifier already synced

## Question
rewards/BribeRewardPool.sol - withdrawFor() carries updateRewards(_for, rewardTokens) and then _getReward() calls earned() again, so the settlement depends on the two reads agreeing across the intervening state writes. Can an unprivileged attacker controlling the negative delta and whether the claim leg runs, under the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, exploit this through `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` to break the reconciliation between `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` and the invariant that a settlement amount must be read once from a single synchronised source, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: _getReward recomputes earned after the modifier already synced)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: withdrawFor() carries updateRewards(_for, rewardTokens) and then _getReward() calls earned() again, so the settlement depends on the two reads agreeing across the intervening state writes. Precondition: the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances.
- Invariant to test: a settlement amount must be read once from a single synchronised source; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, then assert `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` end identical in both runs.
