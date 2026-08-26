# Q2554: DelegateVoteRewardPool.harvestAll - queued backlog released to whoever holds balance at the flush

## Question
In rewards/DelegateVoteRewardPool.sol, _queueNewRewardsWithoutTransfer() accumulates into queuedRewards while totalSupply is zero and releases the whole backlog on the next call, so a single one-block holder can absorb it. Starting from a state where a keeper castVotes transaction that ends in harvestAll is pending in the mempool, can an unprivileged EOA use `harvestAll()` to leave `earnedRewards returned by claimAllBribes` inconsistent with `IERC20(rewardToken).balanceOf(address(this))`, violating the invariant that a backlog accrued while the pool was empty must not be assignable to one holder and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: queued backlog released to whoever holds balance at the flush)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _queueNewRewardsWithoutTransfer() accumulates into queuedRewards while totalSupply is zero and releases the whole backlog on the next call, so a single one-block holder can absorb it. Precondition: a keeper castVotes transaction that ends in harvestAll is pending in the mempool.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to one holder; concretely, `earnedRewards returned by claimAllBribes` must stay reconciled with `IERC20(rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `harvestAll()`: constrain the setup so that a keeper castVotes transaction that ends in harvestAll is pending in the mempool, fuzz the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone), and assert after every call that a backlog accrued while the pool was empty must not be assignable to one holder.
