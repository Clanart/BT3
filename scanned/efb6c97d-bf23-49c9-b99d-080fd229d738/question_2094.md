# Q2094: DelegateVoteRewardPool.getReward - _getDelegateReward zeroes the entitlement before the transfer

## Question
In rewards/DelegateVoteRewardPool.sol, _getDelegateReward() sets userRewards[token][account] = 0 and then calls safeTransfer, so a token that reverts or under-delivers leaves the entitlement cleared with the value undelivered. Starting from a state where a bribe token has a transfer hook the attacker controls, can an unprivileged EOA use `getReward(address _for)` to leave `earnedRewards returned by claimAllBribes` inconsistent with `IERC20(rewardToken).balanceOf(address(this))`, violating the invariant that an entitlement may only be cleared once the exact amount has been delivered and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: _getDelegateReward zeroes the entitlement before the transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: _getDelegateReward() sets userRewards[token][account] = 0 and then calls safeTransfer, so a token that reverts or under-delivers leaves the entitlement cleared with the value undelivered. Precondition: a bribe token has a transfer hook the attacker controls.
- Invariant to test: an entitlement may only be cleared once the exact amount has been delivered; concretely, `earnedRewards returned by claimAllBribes` must stay reconciled with `IERC20(rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getReward(address _for)` sequence atomically under a bribe token has a transfer hook the attacker controls, asserting at the end that `earnedRewards returned by claimAllBribes` still equals `IERC20(rewardToken).balanceOf(address(this))` and the PoC's balance delta is non-positive.
