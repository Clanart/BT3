# Q0855: BribeRewardPool.withdrawFor - _getReward recomputes earned after the modifier already synced

## Question
Consider rewards/BribeRewardPool.sol, where withdrawFor() carries updateRewards(_for, rewardTokens) and then _getReward() calls earned() again, so the settlement depends on the two reads agreeing across the intervening state writes. Assuming the attacker votes and casts inside one transaction through voteAndCast, can an unprivileged attacker turn this into a divergence between `_balances[account]` and `totalSupply` via `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`, breaking the invariant that a settlement amount must be read once from a single synchronised source and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: _getReward recomputes earned after the modifier already synced)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: withdrawFor() carries updateRewards(_for, rewardTokens) and then _getReward() calls earned() again, so the settlement depends on the two reads agreeing across the intervening state writes. Precondition: the attacker votes and casts inside one transaction through voteAndCast.
- Invariant to test: a settlement amount must be read once from a single synchronised source; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` sequence atomically under the attacker votes and casts inside one transaction through voteAndCast, asserting at the end that `_balances[account]` still equals `totalSupply` and the PoC's balance delta is non-positive.
