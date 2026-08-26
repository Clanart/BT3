# Q2002: DelegateVoteRewardPool.harvestAll - delegate votes are excluded from totalVlMgpInVote but included in pool totals

## Question
rewards/DelegateVoteRewardPool.sol: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. With the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone under attacker control and a bribe token has a transfer hook the attacker controls, can an unprivileged caller sequence `harvestAll()` so that `votingWeights[pool] and totalWeight` and `the deltas pushed by _updateVote` no longer reconcile, violating the invariant that a vote counted in a pool total must also be counted in the denominator used to scale that total and realising Critical - Governance voting result manipulation?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: delegate votes are excluded from totalVlMgpInVote but included in pool totals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Precondition: a bribe token has a transfer hook the attacker controls.
- Invariant to test: a vote counted in a pool total must also be counted in the denominator used to scale that total; concretely, `votingWeights[pool] and totalWeight` must stay reconciled with `the deltas pushed by _updateVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone) under a bribe token has a transfer hook the attacker controls, asserting on every row that a vote counted in a pool total must also be counted in the denominator used to scale that total.
