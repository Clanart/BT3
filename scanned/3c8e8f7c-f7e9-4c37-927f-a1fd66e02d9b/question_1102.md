# Q1102: DelegateVoteRewardPool.harvestAll - balance held at the instant of the queue captures the whole stream

## Question
In rewards/DelegateVoteRewardPool.sol, because _queueNewRewardsWithoutTransfer raises rewardPerTokenStored against the live totalSupply and harvestAll is permissionless, an attacker who obtains delegate-pool balance immediately before calling captures a share of a full epoch. Can an unprivileged attacker reach this through `harvestAll()` while the attacker obtains delegate-pool balance in the block before a large bribe lands, and drive `earnedRewards returned by claimAllBribes` out of agreement with `IERC20(rewardToken).balanceOf(address(this))` - breaking the invariant that reward share must be weighted by the time balance was actually held - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: balance held at the instant of the queue captures the whole stream)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: because _queueNewRewardsWithoutTransfer raises rewardPerTokenStored against the live totalSupply and harvestAll is permissionless, an attacker who obtains delegate-pool balance immediately before calling captures a share of a full epoch. Precondition: the attacker obtains delegate-pool balance in the block before a large bribe lands.
- Invariant to test: reward share must be weighted by the time balance was actually held; concretely, `earnedRewards returned by claimAllBribes` must stay reconciled with `IERC20(rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker obtains delegate-pool balance in the block before a large bribe lands, call `harvestAll()`, and assert `earnedRewards returned by claimAllBribes` equals `IERC20(rewardToken).balanceOf(address(this))` and that no account can withdraw more than it put in.
