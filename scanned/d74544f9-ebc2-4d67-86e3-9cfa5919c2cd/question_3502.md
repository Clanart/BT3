# Q3502: BribeRewardPool.withdrawFor - _getReward clears entitlement before the transfer settles

## Question
In rewards/BribeRewardPool.sol, _getReward() reads earned(), writes userRewards[token][account] = 0 and then calls safeTransfer, so an under-delivering or reverting bribe token leaves the entitlement cleared with nothing received. Can an unprivileged attacker reach this through `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` while the attacker calls the inherited donateRewards for the registered bribe token, and drive `_balances[account]` out of agreement with `totalSupply` - breaking the invariant that an entitlement may only be cleared once the exact amount has been delivered - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: _getReward clears entitlement before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: _getReward() reads earned(), writes userRewards[token][account] = 0 and then calls safeTransfer, so an under-delivering or reverting bribe token leaves the entitlement cleared with nothing received. Precondition: the attacker calls the inherited donateRewards for the registered bribe token.
- Invariant to test: an entitlement may only be cleared once the exact amount has been delivered; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker calls the inherited donateRewards for the registered bribe token, then assert `_balances[account]` and `totalSupply` end identical in both runs.
