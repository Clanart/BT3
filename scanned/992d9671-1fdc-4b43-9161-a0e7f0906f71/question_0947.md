# Q0947: DelegateVoteRewardPool.getReward - _getDelegateReward zeroes the entitlement before the transfer

## Question
In rewards/DelegateVoteRewardPool.sol, _getDelegateReward() sets userRewards[token][account] = 0 and then calls safeTransfer, so a token that reverts or under-delivers leaves the entitlement cleared with the value undelivered. Can an unprivileged attacker reach this through `getReward(address _for)` while totalSupply is zero and queuedRewards holds a backlog, and drive `votingWeights[pool] and totalWeight` out of agreement with `the deltas pushed by _updateVote` - breaking the invariant that an entitlement may only be cleared once the exact amount has been delivered - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: _getDelegateReward zeroes the entitlement before the transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: _getDelegateReward() sets userRewards[token][account] = 0 and then calls safeTransfer, so a token that reverts or under-delivers leaves the entitlement cleared with the value undelivered. Precondition: totalSupply is zero and queuedRewards holds a backlog.
- Invariant to test: an entitlement may only be cleared once the exact amount has been delivered; concretely, `votingWeights[pool] and totalWeight` must stay reconciled with `the deltas pushed by _updateVote`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange totalSupply is zero and queuedRewards holds a backlog, call `getReward(address _for)`, and assert `votingWeights[pool] and totalWeight` equals `the deltas pushed by _updateVote` and that no account can withdraw more than it put in.
