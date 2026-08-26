# Q1441: DelegateVoteRewardPool.harvestAll - protocol fee taken from a reported number rather than a real balance

## Question
rewards/DelegateVoteRewardPool.sol - _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Can an unprivileged attacker controlling the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone, under protocolFee is non-zero and feeCollector is set, exploit this through `harvestAll()` to break the reconciliation between `votingWeights[pool] and totalWeight` and `the deltas pushed by _updateVote` and the invariant that a fee must be computed from value the contract actually received, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: protocol fee taken from a reported number rather than a real balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Precondition: protocolFee is non-zero and feeCollector is set.
- Invariant to test: a fee must be computed from value the contract actually received; concretely, `votingWeights[pool] and totalWeight` must stay reconciled with `the deltas pushed by _updateVote`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone) under protocolFee is non-zero and feeCollector is set, asserting on every row that a fee must be computed from value the contract actually received.
