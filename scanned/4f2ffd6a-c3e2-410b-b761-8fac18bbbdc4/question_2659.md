# Q2659: DelegateVoteRewardPool.harvestAll - queues rewards it may never have received

## Question
Consider rewards/DelegateVoteRewardPool.sol, where _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Assuming the victim has a large unsettled userRewards balance in the delegate pool, can an unprivileged attacker turn this into a divergence between `protocolFee` and `earnedRewards[index]` via `harvestAll()`, breaking the invariant that the reward index may only be raised against tokens the contract has actually received and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: queues rewards it may never have received)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Precondition: the victim has a large unsettled userRewards balance in the delegate pool.
- Invariant to test: the reward index may only be raised against tokens the contract has actually received; concretely, `protocolFee` must stay reconciled with `earnedRewards[index]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the victim has a large unsettled userRewards balance in the delegate pool, snapshot `protocolFee` and `earnedRewards[index]`, run the attacker's `harvestAll()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
