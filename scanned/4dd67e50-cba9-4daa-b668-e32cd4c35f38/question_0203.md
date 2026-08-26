# Q0203: DelegateVoteRewardPool.harvestAll - queued backlog released to whoever holds balance at the flush

## Question
rewards/DelegateVoteRewardPool.sol - _queueNewRewardsWithoutTransfer() accumulates into queuedRewards while totalSupply is zero and releases the whole backlog on the next call, so a single one-block holder can absorb it. Can an unprivileged attacker controlling the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone, under the bribe contract for a voted pool registers more than one reward token, exploit this through `harvestAll()` to break the reconciliation between `_balances[account]` and `totalSupply` and the invariant that a backlog accrued while the pool was empty must not be assignable to one holder, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: queued backlog released to whoever holds balance at the flush)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _queueNewRewardsWithoutTransfer() accumulates into queuedRewards while totalSupply is zero and releases the whole backlog on the next call, so a single one-block holder can absorb it. Precondition: the bribe contract for a voted pool registers more than one reward token.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to one holder; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `harvestAll()`: constrain the setup so that the bribe contract for a voted pool registers more than one reward token, fuzz the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone), and assert after every call that a backlog accrued while the pool was empty must not be assignable to one holder.
