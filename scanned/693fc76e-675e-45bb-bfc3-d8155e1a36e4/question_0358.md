# Q0358: DelegateVoteRewardPool.harvestAll - queues rewards it may never have received

## Question
In rewards/DelegateVoteRewardPool.sol, _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Does `harvestAll()` let an unprivileged caller exploit that under the pool rewarder holds less than the earned figure claimAllBribes reported, so that `rewards[_rewardToken].rewardPerTokenStored` diverges from `totalSupply of the delegate pool`, the invariant that the reward index may only be raised against tokens the contract has actually received is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: queues rewards it may never have received)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Precondition: the pool rewarder holds less than the earned figure claimAllBribes reported.
- Invariant to test: the reward index may only be raised against tokens the contract has actually received; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `totalSupply of the delegate pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool rewarder holds less than the earned figure claimAllBribes reported, call `harvestAll()`, and assert `rewards[_rewardToken].rewardPerTokenStored` equals `totalSupply of the delegate pool` and that no account can withdraw more than it put in.
