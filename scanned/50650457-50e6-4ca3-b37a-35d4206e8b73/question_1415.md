# Q1415: BribeRewardPool.withdrawFor - _getReward clears entitlement before the transfer settles

## Question
rewards/BribeRewardPool.sol: _getReward() reads earned(), writes userRewards[token][account] = 0 and then calls safeTransfer, so an under-delivering or reverting bribe token leaves the entitlement cleared with nothing received. With the negative delta and whether the claim leg runs under attacker control and totalSupply is zero because every voter has unvoted, can an unprivileged caller sequence `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` so that `_balances[account]` and `totalSupply` no longer reconcile, violating the invariant that an entitlement may only be cleared once the exact amount has been delivered and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: _getReward clears entitlement before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: _getReward() reads earned(), writes userRewards[token][account] = 0 and then calls safeTransfer, so an under-delivering or reverting bribe token leaves the entitlement cleared with nothing received. Precondition: totalSupply is zero because every voter has unvoted.
- Invariant to test: an entitlement may only be cleared once the exact amount has been delivered; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` sequence atomically under totalSupply is zero because every voter has unvoted, asserting at the end that `_balances[account]` still equals `totalSupply` and the PoC's balance delta is non-positive.
