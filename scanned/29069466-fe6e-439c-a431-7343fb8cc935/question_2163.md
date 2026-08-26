# Q2163: DelegateVoteRewardPool.harvestAll - queues rewards it may never have received

## Question
Consider rewards/DelegateVoteRewardPool.sol, where _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Assuming the delegated pool holds a dominant share of one pool's totalVoteInVlmgp, can an unprivileged attacker turn this into a divergence between `rewards[_rewardToken].rewardPerTokenStored` and `totalSupply of the delegate pool` via `harvestAll()`, breaking the invariant that the reward index may only be raised against tokens the contract has actually received and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: queues rewards it may never have received)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Precondition: the delegated pool holds a dominant share of one pool's totalVoteInVlmgp.
- Invariant to test: the reward index may only be raised against tokens the contract has actually received; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `totalSupply of the delegate pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone) under the delegated pool holds a dominant share of one pool's totalVoteInVlmgp, asserting on every row that the reward index may only be raised against tokens the contract has actually received.
