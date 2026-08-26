# Q3519: BribeRewardPool.withdrawFor - _getReward recomputes earned after the modifier already synced

## Question
Consider rewards/BribeRewardPool.sol, where withdrawFor() carries updateRewards(_for, rewardTokens) and then _getReward() calls earned() again, so the settlement depends on the two reads agreeing across the intervening state writes. Assuming the attacker calls the inherited donateRewards for the registered bribe token, can an unprivileged attacker turn this into a divergence between `totalSupply` and `the sum of userVotedForPoolInVlmgp over all voters for this pool` via `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`, breaking the invariant that a settlement amount must be read once from a single synchronised source and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: _getReward recomputes earned after the modifier already synced)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: withdrawFor() carries updateRewards(_for, rewardTokens) and then _getReward() calls earned() again, so the settlement depends on the two reads agreeing across the intervening state writes. Precondition: the attacker calls the inherited donateRewards for the registered bribe token.
- Invariant to test: a settlement amount must be read once from a single synchronised source; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker calls the inherited donateRewards for the registered bribe token, snapshot `totalSupply` and `the sum of userVotedForPoolInVlmgp over all voters for this pool`, run the attacker's `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
