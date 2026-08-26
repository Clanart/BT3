# Q1648: DelegateVoteRewardPool.harvestAll - queues rewards it may never have received

## Question
rewards/DelegateVoteRewardPool.sol: _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Under protocolFee is zero so the whole reported amount is queued, is there an unprivileged sequence of `harvestAll()` that leaves `userRewards[_rewardToken][account]` unreconciled with `userRewardPerTokenPaid[_rewardToken][account]`, violates the invariant that the reward index may only be raised against tokens the contract has actually received, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: queues rewards it may never have received)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Precondition: protocolFee is zero so the whole reported amount is queued.
- Invariant to test: the reward index may only be raised against tokens the contract has actually received; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up protocolFee is zero so the whole reported amount is queued, snapshot `userRewards[_rewardToken][account]` and `userRewardPerTokenPaid[_rewardToken][account]`, run the attacker's `harvestAll()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
