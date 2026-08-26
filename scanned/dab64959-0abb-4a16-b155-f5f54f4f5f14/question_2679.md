# Q2679: DelegateVoteRewardPool.harvestAll - harvestAll is permissionless and sets the distribution instant

## Question
In rewards/DelegateVoteRewardPool.sol, harvestAll() has no caller restriction, so any address decides the block at which the entire delegate bribe stream is claimed, fee-charged and folded into rewardPerTokenStored. Can an unprivileged attacker reach this through `harvestAll()` while the victim has a large unsettled userRewards balance in the delegate pool, and drive `userRewards[_rewardToken][account]` out of agreement with `userRewardPerTokenPaid[_rewardToken][account]` - breaking the invariant that the instant at which a shared reward stream is distributed must not be selectable by an unrelated party - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: harvestAll is permissionless and sets the distribution instant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: harvestAll() has no caller restriction, so any address decides the block at which the entire delegate bribe stream is claimed, fee-charged and folded into rewardPerTokenStored. Precondition: the victim has a large unsettled userRewards balance in the delegate pool.
- Invariant to test: the instant at which a shared reward stream is distributed must not be selectable by an unrelated party; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the victim has a large unsettled userRewards balance in the delegate pool, call `harvestAll()`, and assert `userRewards[_rewardToken][account]` equals `userRewardPerTokenPaid[_rewardToken][account]` and that no account can withdraw more than it put in.
