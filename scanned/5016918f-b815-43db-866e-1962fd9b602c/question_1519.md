# Q1519: DelegateVoteRewardPool.harvestAll - queued backlog released to whoever holds balance at the flush

## Question
In rewards/DelegateVoteRewardPool.sol, _queueNewRewardsWithoutTransfer() accumulates into queuedRewards while totalSupply is zero and releases the whole backlog on the next call, so a single one-block holder can absorb it. Can an unprivileged attacker reach this through `harvestAll()` while protocolFee is non-zero and feeCollector is set, and drive `votingWeights[pool] and totalWeight` out of agreement with `the deltas pushed by _updateVote` - breaking the invariant that a backlog accrued while the pool was empty must not be assignable to one holder - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: queued backlog released to whoever holds balance at the flush)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _queueNewRewardsWithoutTransfer() accumulates into queuedRewards while totalSupply is zero and releases the whole backlog on the next call, so a single one-block holder can absorb it. Precondition: protocolFee is non-zero and feeCollector is set.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to one holder; concretely, `votingWeights[pool] and totalWeight` must stay reconciled with `the deltas pushed by _updateVote`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone) under protocolFee is non-zero and feeCollector is set, asserting on every row that a backlog accrued while the pool was empty must not be assignable to one holder.
