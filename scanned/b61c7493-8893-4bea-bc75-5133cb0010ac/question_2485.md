# Q2485: DelegateVoteRewardPool.harvestAll - protocol fee taken from a reported number rather than a real balance

## Question
In rewards/DelegateVoteRewardPool.sol, _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Can an unprivileged attacker reach this through `harvestAll()` while a keeper castVotes transaction that ends in harvestAll is pending in the mempool, and drive `earnedRewards returned by claimAllBribes` out of agreement with `IERC20(rewardToken).balanceOf(address(this))` - breaking the invariant that a fee must be computed from value the contract actually received - for Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: protocol fee taken from a reported number rather than a real balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Precondition: a keeper castVotes transaction that ends in harvestAll is pending in the mempool.
- Invariant to test: a fee must be computed from value the contract actually received; concretely, `earnedRewards returned by claimAllBribes` must stay reconciled with `IERC20(rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `harvestAll()` sequence atomically under a keeper castVotes transaction that ends in harvestAll is pending in the mempool, asserting at the end that `earnedRewards returned by claimAllBribes` still equals `IERC20(rewardToken).balanceOf(address(this))` and the PoC's balance delta is non-positive.
