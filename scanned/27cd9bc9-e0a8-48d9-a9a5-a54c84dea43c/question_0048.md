# Q0048: DelegateVoteRewardPool.harvestAll - harvestAll is permissionless and sets the distribution instant

## Question
rewards/DelegateVoteRewardPool.sol: harvestAll() has no caller restriction, so any address decides the block at which the entire delegate bribe stream is claimed, fee-charged and folded into rewardPerTokenStored. With the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone under attacker control and the bribe contract for a voted pool registers more than one reward token, can an unprivileged caller sequence `harvestAll()` so that `votingWeights[pool] and totalWeight` and `the deltas pushed by _updateVote` no longer reconcile, violating the invariant that the instant at which a shared reward stream is distributed must not be selectable by an unrelated party and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: harvestAll is permissionless and sets the distribution instant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: harvestAll() has no caller restriction, so any address decides the block at which the entire delegate bribe stream is claimed, fee-charged and folded into rewardPerTokenStored. Precondition: the bribe contract for a voted pool registers more than one reward token.
- Invariant to test: the instant at which a shared reward stream is distributed must not be selectable by an unrelated party; concretely, `votingWeights[pool] and totalWeight` must stay reconciled with `the deltas pushed by _updateVote`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the bribe contract for a voted pool registers more than one reward token, snapshot `votingWeights[pool] and totalWeight` and `the deltas pushed by _updateVote`, run the attacker's `harvestAll()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
