# Q2699: DelegateVoteRewardPool.harvestAll - balance held at the instant of the queue captures the whole stream

## Question
In rewards/DelegateVoteRewardPool.sol, because _queueNewRewardsWithoutTransfer raises rewardPerTokenStored against the live totalSupply and harvestAll is permissionless, an attacker who obtains delegate-pool balance immediately before calling captures a share of a full epoch. Does `harvestAll()` let an unprivileged caller exploit that under the victim has a large unsettled userRewards balance in the delegate pool, so that `earnedRewards returned by claimAllBribes` diverges from `IERC20(rewardToken).balanceOf(address(this))`, the invariant that reward share must be weighted by the time balance was actually held is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: balance held at the instant of the queue captures the whole stream)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: because _queueNewRewardsWithoutTransfer raises rewardPerTokenStored against the live totalSupply and harvestAll is permissionless, an attacker who obtains delegate-pool balance immediately before calling captures a share of a full epoch. Precondition: the victim has a large unsettled userRewards balance in the delegate pool.
- Invariant to test: reward share must be weighted by the time balance was actually held; concretely, `earnedRewards returned by claimAllBribes` must stay reconciled with `IERC20(rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the victim has a large unsettled userRewards balance in the delegate pool, call `harvestAll()`, and assert `earnedRewards returned by claimAllBribes` equals `IERC20(rewardToken).balanceOf(address(this))` and that no account can withdraw more than it put in.
