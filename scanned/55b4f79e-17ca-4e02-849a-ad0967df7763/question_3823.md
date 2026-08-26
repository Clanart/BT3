# Q3823: BribeRewardPool.withdrawFor - _getReward clears entitlement before the transfer settles

## Question
rewards/BribeRewardPool.sol: _getReward() reads earned(), writes userRewards[token][account] = 0 and then calls safeTransfer, so an under-delivering or reverting bribe token leaves the entitlement cleared with nothing received. With the negative delta and whether the claim leg runs under attacker control and the victim has a large unsettled bribe balance, can an unprivileged caller sequence `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` so that `totalSupply` and `the sum of userVotedForPoolInVlmgp over all voters for this pool` no longer reconcile, violating the invariant that an entitlement may only be cleared once the exact amount has been delivered and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: _getReward clears entitlement before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: _getReward() reads earned(), writes userRewards[token][account] = 0 and then calls safeTransfer, so an under-delivering or reverting bribe token leaves the entitlement cleared with nothing received. Precondition: the victim has a large unsettled bribe balance.
- Invariant to test: an entitlement may only be cleared once the exact amount has been delivered; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the victim has a large unsettled bribe balance, then assert `totalSupply` and `the sum of userVotedForPoolInVlmgp over all voters for this pool` end identical in both runs.
