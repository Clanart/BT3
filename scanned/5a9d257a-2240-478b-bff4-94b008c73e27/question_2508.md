# Q2508: DelegateVoteRewardPool.harvestAll - delegate votes are excluded from totalVlMgpInVote but included in pool totals

## Question
In rewards/DelegateVoteRewardPool.sol, WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Starting from a state where a keeper castVotes transaction that ends in harvestAll is pending in the mempool, can an unprivileged EOA use `harvestAll()` to leave `_balances[account]` inconsistent with `totalSupply`, violating the invariant that a vote counted in a pool total must also be counted in the denominator used to scale that total and extracting Critical - Governance voting result manipulation?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: delegate votes are excluded from totalVlMgpInVote but included in pool totals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Precondition: a keeper castVotes transaction that ends in harvestAll is pending in the mempool.
- Invariant to test: a vote counted in a pool total must also be counted in the denominator used to scale that total; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a keeper castVotes transaction that ends in harvestAll is pending in the mempool, then assert `_balances[account]` and `totalSupply` end identical in both runs.
