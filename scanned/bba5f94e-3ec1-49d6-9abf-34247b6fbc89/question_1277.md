# Q1277: DelegateVoteRewardPool.getReward - _getDelegateReward zeroes the entitlement before the transfer

## Question
Consider rewards/DelegateVoteRewardPool.sol, where _getDelegateReward() sets userRewards[token][account] = 0 and then calls safeTransfer, so a token that reverts or under-delivers leaves the entitlement cleared with the value undelivered. Assuming the attacker obtains delegate-pool balance in the block before a large bribe lands, can an unprivileged attacker turn this into a divergence between `protocolFee` and `earnedRewards[index]` via `getReward(address _for)`, breaking the invariant that an entitlement may only be cleared once the exact amount has been delivered and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: _getDelegateReward zeroes the entitlement before the transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: _getDelegateReward() sets userRewards[token][account] = 0 and then calls safeTransfer, so a token that reverts or under-delivers leaves the entitlement cleared with the value undelivered. Precondition: the attacker obtains delegate-pool balance in the block before a large bribe lands.
- Invariant to test: an entitlement may only be cleared once the exact amount has been delivered; concretely, `protocolFee` must stay reconciled with `earnedRewards[index]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker obtains delegate-pool balance in the block before a large bribe lands, then assert `protocolFee` and `earnedRewards[index]` end identical in both runs.
