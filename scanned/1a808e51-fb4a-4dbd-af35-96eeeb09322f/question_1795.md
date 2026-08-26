# Q1795: DelegateVoteRewardPool.harvestAll - queued backlog released to whoever holds balance at the flush

## Question
rewards/DelegateVoteRewardPool.sol - _queueNewRewardsWithoutTransfer() accumulates into queuedRewards while totalSupply is zero and releases the whole backlog on the next call, so a single one-block holder can absorb it. Can an unprivileged attacker controlling the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone, under protocolFee is zero so the whole reported amount is queued, exploit this through `harvestAll()` to break the reconciliation between `protocolFee` and `earnedRewards[index]` and the invariant that a backlog accrued while the pool was empty must not be assignable to one holder, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: queued backlog released to whoever holds balance at the flush)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _queueNewRewardsWithoutTransfer() accumulates into queuedRewards while totalSupply is zero and releases the whole backlog on the next call, so a single one-block holder can absorb it. Precondition: protocolFee is zero so the whole reported amount is queued.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to one holder; concretely, `protocolFee` must stay reconciled with `earnedRewards[index]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under protocolFee is zero so the whole reported amount is queued, then assert `protocolFee` and `earnedRewards[index]` end identical in both runs.
