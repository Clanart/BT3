# Q0544: DelegateVoteRewardPool.harvestAll - queued backlog released to whoever holds balance at the flush

## Question
rewards/DelegateVoteRewardPool.sol: _queueNewRewardsWithoutTransfer() accumulates into queuedRewards while totalSupply is zero and releases the whole backlog on the next call, so a single one-block holder can absorb it. With the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone under attacker control and the pool rewarder holds less than the earned figure claimAllBribes reported, can an unprivileged caller sequence `harvestAll()` so that `userRewards[_rewardToken][account]` and `userRewardPerTokenPaid[_rewardToken][account]` no longer reconcile, violating the invariant that a backlog accrued while the pool was empty must not be assignable to one holder and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: queued backlog released to whoever holds balance at the flush)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _queueNewRewardsWithoutTransfer() accumulates into queuedRewards while totalSupply is zero and releases the whole backlog on the next call, so a single one-block holder can absorb it. Precondition: the pool rewarder holds less than the earned figure claimAllBribes reported.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to one holder; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool rewarder holds less than the earned figure claimAllBribes reported, call `harvestAll()`, and assert `userRewards[_rewardToken][account]` equals `userRewardPerTokenPaid[_rewardToken][account]` and that no account can withdraw more than it put in.
