# Q1934: BribeRewardPool.withdrawFor - _getReward recomputes earned after the modifier already synced

## Question
rewards/BribeRewardPool.sol: withdrawFor() carries updateRewards(_for, rewardTokens) and then _getReward() calls earned() again, so the settlement depends on the two reads agreeing across the intervening state writes. With the negative delta and whether the claim leg runs under attacker control and the bribe token registered for this gauge charges a transfer fee, can an unprivileged caller sequence `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` so that `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` no longer reconcile, violating the invariant that a settlement amount must be read once from a single synchronised source and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: _getReward recomputes earned after the modifier already synced)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: withdrawFor() carries updateRewards(_for, rewardTokens) and then _getReward() calls earned() again, so the settlement depends on the two reads agreeing across the intervening state writes. Precondition: the bribe token registered for this gauge charges a transfer fee.
- Invariant to test: a settlement amount must be read once from a single synchronised source; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the bribe token registered for this gauge charges a transfer fee, snapshot `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]`, run the attacker's `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
