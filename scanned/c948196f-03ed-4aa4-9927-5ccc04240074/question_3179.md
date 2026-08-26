# Q3179: BribeRewardPool.withdrawFor - _getReward recomputes earned after the modifier already synced

## Question
Note that in rewards/BribeRewardPool.sol, withdrawFor() carries updateRewards(_for, rewardTokens) and then _getReward() calls earned() again, so the settlement depends on the two reads agreeing across the intervening state writes. Can an attacker holding only tokens bought on market reach it via `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` under the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor and force `_balances[account]` apart from `totalSupply`, breaking the invariant that a settlement amount must be read once from a single synchronised source for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: _getReward recomputes earned after the modifier already synced)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: withdrawFor() carries updateRewards(_for, rewardTokens) and then _getReward() calls earned() again, so the settlement depends on the two reads agreeing across the intervening state writes. Precondition: the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor.
- Invariant to test: a settlement amount must be read once from a single synchronised source; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`: constrain the setup so that the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, fuzz the attacker inputs (the negative delta and whether the claim leg runs), and assert after every call that a settlement amount must be read once from a single synchronised source.
