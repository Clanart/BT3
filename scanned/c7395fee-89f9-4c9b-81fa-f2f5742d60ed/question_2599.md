# Q2599: DelegateVoteRewardPool.getReward - _getDelegateReward zeroes the entitlement before the transfer

## Question
rewards/DelegateVoteRewardPool.sol - _getDelegateReward() sets userRewards[token][account] = 0 and then calls safeTransfer, so a token that reverts or under-delivers leaves the entitlement cleared with the value undelivered. Can an unprivileged attacker controlling _for (any victim) and the settlement timing, under a keeper castVotes transaction that ends in harvestAll is pending in the mempool, exploit this through `getReward(address _for)` to break the reconciliation between `votingWeights[pool] and totalWeight` and `the deltas pushed by _updateVote` and the invariant that an entitlement may only be cleared once the exact amount has been delivered, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: _getDelegateReward zeroes the entitlement before the transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: _getDelegateReward() sets userRewards[token][account] = 0 and then calls safeTransfer, so a token that reverts or under-delivers leaves the entitlement cleared with the value undelivered. Precondition: a keeper castVotes transaction that ends in harvestAll is pending in the mempool.
- Invariant to test: an entitlement may only be cleared once the exact amount has been delivered; concretely, `votingWeights[pool] and totalWeight` must stay reconciled with `the deltas pushed by _updateVote`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up a keeper castVotes transaction that ends in harvestAll is pending in the mempool, snapshot `votingWeights[pool] and totalWeight` and `the deltas pushed by _updateVote`, run the attacker's `getReward(address _for)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
