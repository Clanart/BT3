# Q0823: DelegateVoteRewardPool.harvestAll - delegate votes are excluded from totalVlMgpInVote but included in pool totals

## Question
Note that in rewards/DelegateVoteRewardPool.sol, WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Can an attacker holding only tokens bought on market reach it via `harvestAll()` under totalSupply is zero and queuedRewards holds a backlog and force `_balances[account]` apart from `totalSupply`, breaking the invariant that a vote counted in a pool total must also be counted in the denominator used to scale that total for Critical - Governance voting result manipulation?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: delegate votes are excluded from totalVlMgpInVote but included in pool totals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Precondition: totalSupply is zero and queuedRewards holds a backlog.
- Invariant to test: a vote counted in a pool total must also be counted in the denominator used to scale that total; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone) under totalSupply is zero and queuedRewards holds a backlog, asserting on every row that a vote counted in a pool total must also be counted in the denominator used to scale that total.
