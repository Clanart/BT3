# Q0792: DelegateVoteRewardPool.harvestAll - protocol fee taken from a reported number rather than a real balance

## Question
In rewards/DelegateVoteRewardPool.sol, _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Starting from a state where totalSupply is zero and queuedRewards holds a backlog, can an unprivileged EOA use `harvestAll()` to leave `earnedRewards returned by claimAllBribes` inconsistent with `IERC20(rewardToken).balanceOf(address(this))`, violating the invariant that a fee must be computed from value the contract actually received and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: protocol fee taken from a reported number rather than a real balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Precondition: totalSupply is zero and queuedRewards holds a backlog.
- Invariant to test: a fee must be computed from value the contract actually received; concretely, `earnedRewards returned by claimAllBribes` must stay reconciled with `IERC20(rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone) under totalSupply is zero and queuedRewards holds a backlog, asserting on every row that a fee must be computed from value the contract actually received.
