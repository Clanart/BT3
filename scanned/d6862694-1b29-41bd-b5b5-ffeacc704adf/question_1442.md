# Q1442: BribeRewardPool.withdrawFor - _getReward recomputes earned after the modifier already synced

## Question
In rewards/BribeRewardPool.sol, withdrawFor() carries updateRewards(_for, rewardTokens) and then _getReward() calls earned() again, so the settlement depends on the two reads agreeing across the intervening state writes. Does `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` let an unprivileged caller exploit that under totalSupply is zero because every voter has unvoted, so that `totalSupply` diverges from `the sum of userVotedForPoolInVlmgp over all voters for this pool`, the invariant that a settlement amount must be read once from a single synchronised source is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: _getReward recomputes earned after the modifier already synced)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: withdrawFor() carries updateRewards(_for, rewardTokens) and then _getReward() calls earned() again, so the settlement depends on the two reads agreeing across the intervening state writes. Precondition: totalSupply is zero because every voter has unvoted.
- Invariant to test: a settlement amount must be read once from a single synchronised source; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` sequence atomically under totalSupply is zero because every voter has unvoted, asserting at the end that `totalSupply` still equals `the sum of userVotedForPoolInVlmgp over all voters for this pool` and the PoC's balance delta is non-positive.
