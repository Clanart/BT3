# Q2371: BribeRewardPool.withdrawFor - _getReward clears entitlement before the transfer settles

## Question
In rewards/BribeRewardPool.sol, _getReward() reads earned(), writes userRewards[token][account] = 0 and then calls safeTransfer, so an under-delivering or reverting bribe token leaves the entitlement cleared with nothing received. Does `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` let an unprivileged caller exploit that under the bribe token has begun reverting on transfer, so that `rewards[_rewardToken].rewardPerTokenStored` diverges from `userRewardPerTokenPaid[_rewardToken][account]`, the invariant that an entitlement may only be cleared once the exact amount has been delivered is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: _getReward clears entitlement before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: _getReward() reads earned(), writes userRewards[token][account] = 0 and then calls safeTransfer, so an under-delivering or reverting bribe token leaves the entitlement cleared with nothing received. Precondition: the bribe token has begun reverting on transfer.
- Invariant to test: an entitlement may only be cleared once the exact amount has been delivered; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the bribe token has begun reverting on transfer, then assert `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` end identical in both runs.
