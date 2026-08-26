# Q0235: BribeRewardPool.withdrawFor - _getReward recomputes earned after the modifier already synced

## Question
Note that in rewards/BribeRewardPool.sol, withdrawFor() carries updateRewards(_for, rewardTokens) and then _getReward() calls earned() again, so the settlement depends on the two reads agreeing across the intervening state writes. Can an attacker holding only tokens bought on market reach it via `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` under a large bribe for the gauge is pending and no cast has run yet and force `rewards[_rewardToken].queuedRewards` apart from `totalSupply at the moment of the flush`, breaking the invariant that a settlement amount must be read once from a single synchronised source for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: _getReward recomputes earned after the modifier already synced)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: withdrawFor() carries updateRewards(_for, rewardTokens) and then _getReward() calls earned() again, so the settlement depends on the two reads agreeing across the intervening state writes. Precondition: a large bribe for the gauge is pending and no cast has run yet.
- Invariant to test: a settlement amount must be read once from a single synchronised source; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the negative delta and whether the claim leg runs) under a large bribe for the gauge is pending and no cast has run yet, asserting on every row that a settlement amount must be read once from a single synchronised source.
