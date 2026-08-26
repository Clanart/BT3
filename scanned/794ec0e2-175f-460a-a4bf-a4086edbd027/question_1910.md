# Q1910: DelegateVoteRewardPool.harvestAll - queues rewards it may never have received

## Question
In rewards/DelegateVoteRewardPool.sol, _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Does `harvestAll()` let an unprivileged caller exploit that under a bribe token has a transfer hook the attacker controls, so that `earnedRewards returned by claimAllBribes` diverges from `IERC20(rewardToken).balanceOf(address(this))`, the invariant that the reward index may only be raised against tokens the contract has actually received is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: queues rewards it may never have received)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Precondition: a bribe token has a transfer hook the attacker controls.
- Invariant to test: the reward index may only be raised against tokens the contract has actually received; concretely, `earnedRewards returned by claimAllBribes` must stay reconciled with `IERC20(rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `harvestAll()` sequence atomically under a bribe token has a transfer hook the attacker controls, asserting at the end that `earnedRewards returned by claimAllBribes` still equals `IERC20(rewardToken).balanceOf(address(this))` and the PoC's balance delta is non-positive.
