# Q1387: DelegateVoteRewardPool.harvestAll - harvestAll is permissionless and sets the distribution instant

## Question
rewards/DelegateVoteRewardPool.sol - harvestAll() has no caller restriction, so any address decides the block at which the entire delegate bribe stream is claimed, fee-charged and folded into rewardPerTokenStored. Can an unprivileged attacker controlling the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone, under protocolFee is non-zero and feeCollector is set, exploit this through `harvestAll()` to break the reconciliation between `earnedRewards returned by claimAllBribes` and `IERC20(rewardToken).balanceOf(address(this))` and the invariant that the instant at which a shared reward stream is distributed must not be selectable by an unrelated party, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: harvestAll is permissionless and sets the distribution instant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: harvestAll() has no caller restriction, so any address decides the block at which the entire delegate bribe stream is claimed, fee-charged and folded into rewardPerTokenStored. Precondition: protocolFee is non-zero and feeCollector is set.
- Invariant to test: the instant at which a shared reward stream is distributed must not be selectable by an unrelated party; concretely, `earnedRewards returned by claimAllBribes` must stay reconciled with `IERC20(rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone) under protocolFee is non-zero and feeCollector is set, asserting on every row that the instant at which a shared reward stream is distributed must not be selectable by an unrelated party.
