# Q1162: DelegateVoteRewardPool.harvestAll - delegate votes are excluded from totalVlMgpInVote but included in pool totals

## Question
rewards/DelegateVoteRewardPool.sol: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. With the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone under attacker control and the attacker obtains delegate-pool balance in the block before a large bribe lands, can an unprivileged caller sequence `harvestAll()` so that `userRewards[_rewardToken][account]` and `userRewardPerTokenPaid[_rewardToken][account]` no longer reconcile, violating the invariant that a vote counted in a pool total must also be counted in the denominator used to scale that total and realising Critical - Governance voting result manipulation?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: delegate votes are excluded from totalVlMgpInVote but included in pool totals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Precondition: the attacker obtains delegate-pool balance in the block before a large bribe lands.
- Invariant to test: a vote counted in a pool total must also be counted in the denominator used to scale that total; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker obtains delegate-pool balance in the block before a large bribe lands, then assert `userRewards[_rewardToken][account]` and `userRewardPerTokenPaid[_rewardToken][account]` end identical in both runs.
