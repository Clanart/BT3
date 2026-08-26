# Q0141: DelegateVoteRewardPool.harvestAll - delegate votes are excluded from totalVlMgpInVote but included in pool totals

## Question
rewards/DelegateVoteRewardPool.sol - WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Can an unprivileged attacker controlling the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone, under the bribe contract for a voted pool registers more than one reward token, exploit this through `harvestAll()` to break the reconciliation between `votingWeights[pool] and totalWeight` and `the deltas pushed by _updateVote` and the invariant that a vote counted in a pool total must also be counted in the denominator used to scale that total, yielding Critical - Governance voting result manipulation?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: delegate votes are excluded from totalVlMgpInVote but included in pool totals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Precondition: the bribe contract for a voted pool registers more than one reward token.
- Invariant to test: a vote counted in a pool total must also be counted in the denominator used to scale that total; concretely, `votingWeights[pool] and totalWeight` must stay reconciled with `the deltas pushed by _updateVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange the bribe contract for a voted pool registers more than one reward token, call `harvestAll()`, and assert `votingWeights[pool] and totalWeight` equals `the deltas pushed by _updateVote` and that no account can withdraw more than it put in.
