# Q0824: BribeRewardPool.withdrawFor - _getReward clears entitlement before the transfer settles

## Question
Consider rewards/BribeRewardPool.sol, where _getReward() reads earned(), writes userRewards[token][account] = 0 and then calls safeTransfer, so an under-delivering or reverting bribe token leaves the entitlement cleared with nothing received. Assuming the attacker votes and casts inside one transaction through voteAndCast, can an unprivileged attacker turn this into a divergence between `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` via `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`, breaking the invariant that an entitlement may only be cleared once the exact amount has been delivered and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: _getReward clears entitlement before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: _getReward() reads earned(), writes userRewards[token][account] = 0 and then calls safeTransfer, so an under-delivering or reverting bribe token leaves the entitlement cleared with nothing received. Precondition: the attacker votes and casts inside one transaction through voteAndCast.
- Invariant to test: an entitlement may only be cleared once the exact amount has been delivered; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker votes and casts inside one transaction through voteAndCast, snapshot `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush`, run the attacker's `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
