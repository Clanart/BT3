# Q0265: DelegateVoteRewardPool.getReward - _getDelegateReward zeroes the entitlement before the transfer

## Question
Note that in rewards/DelegateVoteRewardPool.sol, _getDelegateReward() sets userRewards[token][account] = 0 and then calls safeTransfer, so a token that reverts or under-delivers leaves the entitlement cleared with the value undelivered. Can an attacker holding only tokens bought on market reach it via `getReward(address _for)` under the bribe contract for a voted pool registers more than one reward token and force `earnedRewards returned by claimAllBribes` apart from `IERC20(rewardToken).balanceOf(address(this))`, breaking the invariant that an entitlement may only be cleared once the exact amount has been delivered for High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: _getDelegateReward zeroes the entitlement before the transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: _getDelegateReward() sets userRewards[token][account] = 0 and then calls safeTransfer, so a token that reverts or under-delivers leaves the entitlement cleared with the value undelivered. Precondition: the bribe contract for a voted pool registers more than one reward token.
- Invariant to test: an entitlement may only be cleared once the exact amount has been delivered; concretely, `earnedRewards returned by claimAllBribes` must stay reconciled with `IERC20(rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the bribe contract for a voted pool registers more than one reward token, then assert `earnedRewards returned by claimAllBribes` and `IERC20(rewardToken).balanceOf(address(this))` end identical in both runs.
