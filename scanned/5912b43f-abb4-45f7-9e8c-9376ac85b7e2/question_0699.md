# Q0699: DelegateVoteRewardPool.harvestAll - queues rewards it may never have received

## Question
Consider rewards/DelegateVoteRewardPool.sol, where _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Assuming totalSupply is zero and queuedRewards holds a backlog, can an unprivileged attacker turn this into a divergence between `votingWeights[pool] and totalWeight` and `the deltas pushed by _updateVote` via `harvestAll()`, breaking the invariant that the reward index may only be raised against tokens the contract has actually received and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: queues rewards it may never have received)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Precondition: totalSupply is zero and queuedRewards holds a backlog.
- Invariant to test: the reward index may only be raised against tokens the contract has actually received; concretely, `votingWeights[pool] and totalWeight` must stay reconciled with `the deltas pushed by _updateVote`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up totalSupply is zero and queuedRewards holds a backlog, snapshot `votingWeights[pool] and totalWeight` and `the deltas pushed by _updateVote`, run the attacker's `harvestAll()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
