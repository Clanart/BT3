# Q2301: DelegateVoteRewardPool.harvestAll - queued backlog released to whoever holds balance at the flush

## Question
Note that in rewards/DelegateVoteRewardPool.sol, _queueNewRewardsWithoutTransfer() accumulates into queuedRewards while totalSupply is zero and releases the whole backlog on the next call, so a single one-block holder can absorb it. Can an attacker holding only tokens bought on market reach it via `harvestAll()` under the delegated pool holds a dominant share of one pool's totalVoteInVlmgp and force `userRewards[_rewardToken][account]` apart from `userRewardPerTokenPaid[_rewardToken][account]`, breaking the invariant that a backlog accrued while the pool was empty must not be assignable to one holder for Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: queued backlog released to whoever holds balance at the flush)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _queueNewRewardsWithoutTransfer() accumulates into queuedRewards while totalSupply is zero and releases the whole backlog on the next call, so a single one-block holder can absorb it. Precondition: the delegated pool holds a dominant share of one pool's totalVoteInVlmgp.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to one holder; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the delegated pool holds a dominant share of one pool's totalVoteInVlmgp, snapshot `userRewards[_rewardToken][account]` and `userRewardPerTokenPaid[_rewardToken][account]`, run the attacker's `harvestAll()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
