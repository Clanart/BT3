# Q1040: DelegateVoteRewardPool.harvestAll - queues rewards it may never have received

## Question
rewards/DelegateVoteRewardPool.sol: _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Under the attacker obtains delegate-pool balance in the block before a large bribe lands, is there an unprivileged sequence of `harvestAll()` that leaves `protocolFee` unreconciled with `earnedRewards[index]`, violates the invariant that the reward index may only be raised against tokens the contract has actually received, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: queues rewards it may never have received)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Precondition: the attacker obtains delegate-pool balance in the block before a large bribe lands.
- Invariant to test: the reward index may only be raised against tokens the contract has actually received; concretely, `protocolFee` must stay reconciled with `earnedRewards[index]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone) under the attacker obtains delegate-pool balance in the block before a large bribe lands, asserting on every row that the reward index may only be raised against tokens the contract has actually received.
