# Q0017: DelegateVoteRewardPool.harvestAll - queues rewards it may never have received

## Question
In rewards/DelegateVoteRewardPool.sol, _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Can an unprivileged attacker reach this through `harvestAll()` while the bribe contract for a voted pool registers more than one reward token, and drive `earnedRewards returned by claimAllBribes` out of agreement with `IERC20(rewardToken).balanceOf(address(this))` - breaking the invariant that the reward index may only be raised against tokens the contract has actually received - for Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: queues rewards it may never have received)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Precondition: the bribe contract for a voted pool registers more than one reward token.
- Invariant to test: the reward index may only be raised against tokens the contract has actually received; concretely, `earnedRewards returned by claimAllBribes` must stay reconciled with `IERC20(rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `harvestAll()`: constrain the setup so that the bribe contract for a voted pool registers more than one reward token, fuzz the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone), and assert after every call that the reward index may only be raised against tokens the contract has actually received.
