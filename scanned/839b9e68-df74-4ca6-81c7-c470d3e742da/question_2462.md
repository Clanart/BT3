# Q2462: DelegateVoteRewardPool.harvestAll - balance held at the instant of the queue captures the whole stream

## Question
In rewards/DelegateVoteRewardPool.sol, because _queueNewRewardsWithoutTransfer raises rewardPerTokenStored against the live totalSupply and harvestAll is permissionless, an attacker who obtains delegate-pool balance immediately before calling captures a share of a full epoch. Can an unprivileged attacker reach this through `harvestAll()` while a keeper castVotes transaction that ends in harvestAll is pending in the mempool, and drive `userRewards[_rewardToken][account]` out of agreement with `userRewardPerTokenPaid[_rewardToken][account]` - breaking the invariant that reward share must be weighted by the time balance was actually held - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: balance held at the instant of the queue captures the whole stream)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: because _queueNewRewardsWithoutTransfer raises rewardPerTokenStored against the live totalSupply and harvestAll is permissionless, an attacker who obtains delegate-pool balance immediately before calling captures a share of a full epoch. Precondition: a keeper castVotes transaction that ends in harvestAll is pending in the mempool.
- Invariant to test: reward share must be weighted by the time balance was actually held; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange a keeper castVotes transaction that ends in harvestAll is pending in the mempool, call `harvestAll()`, and assert `userRewards[_rewardToken][account]` equals `userRewardPerTokenPaid[_rewardToken][account]` and that no account can withdraw more than it put in.
