# Q2416: DelegateVoteRewardPool.harvestAll - queues rewards it may never have received

## Question
rewards/DelegateVoteRewardPool.sol: _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Under a keeper castVotes transaction that ends in harvestAll is pending in the mempool, is there an unprivileged sequence of `harvestAll()` that leaves `votingWeights[pool] and totalWeight` unreconciled with `the deltas pushed by _updateVote`, violates the invariant that the reward index may only be raised against tokens the contract has actually received, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: queues rewards it may never have received)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Precondition: a keeper castVotes transaction that ends in harvestAll is pending in the mempool.
- Invariant to test: the reward index may only be raised against tokens the contract has actually received; concretely, `votingWeights[pool] and totalWeight` must stay reconciled with `the deltas pushed by _updateVote`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange a keeper castVotes transaction that ends in harvestAll is pending in the mempool, call `harvestAll()`, and assert `votingWeights[pool] and totalWeight` equals `the deltas pushed by _updateVote` and that no account can withdraw more than it put in.
