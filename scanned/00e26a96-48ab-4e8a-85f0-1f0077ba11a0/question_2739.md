# Q2739: DelegateVoteRewardPool.harvestAll - delegate votes are excluded from totalVlMgpInVote but included in pool totals

## Question
Consider rewards/DelegateVoteRewardPool.sol, where WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Assuming the victim has a large unsettled userRewards balance in the delegate pool, can an unprivileged attacker turn this into a divergence between `userRewards[_rewardToken][account]` and `userRewardPerTokenPaid[_rewardToken][account]` via `harvestAll()`, breaking the invariant that a vote counted in a pool total must also be counted in the denominator used to scale that total and producing Critical - Governance voting result manipulation?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: delegate votes are excluded from totalVlMgpInVote but included in pool totals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Precondition: the victim has a large unsettled userRewards balance in the delegate pool.
- Invariant to test: a vote counted in a pool total must also be counted in the denominator used to scale that total; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `harvestAll()` sequence atomically under the victim has a large unsettled userRewards balance in the delegate pool, asserting at the end that `userRewards[_rewardToken][account]` still equals `userRewardPerTokenPaid[_rewardToken][account]` and the PoC's balance delta is non-positive.
