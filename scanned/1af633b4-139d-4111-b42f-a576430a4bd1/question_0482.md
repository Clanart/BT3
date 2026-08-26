# Q0482: DelegateVoteRewardPool.harvestAll - delegate votes are excluded from totalVlMgpInVote but included in pool totals

## Question
rewards/DelegateVoteRewardPool.sol: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. With the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone under attacker control and the pool rewarder holds less than the earned figure claimAllBribes reported, can an unprivileged caller sequence `harvestAll()` so that `protocolFee` and `earnedRewards[index]` no longer reconcile, violating the invariant that a vote counted in a pool total must also be counted in the denominator used to scale that total and realising Critical - Governance voting result manipulation?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: delegate votes are excluded from totalVlMgpInVote but included in pool totals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Precondition: the pool rewarder holds less than the earned figure claimAllBribes reported.
- Invariant to test: a vote counted in a pool total must also be counted in the denominator used to scale that total; concretely, `protocolFee` must stay reconciled with `earnedRewards[index]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up the pool rewarder holds less than the earned figure claimAllBribes reported, snapshot `protocolFee` and `earnedRewards[index]`, run the attacker's `harvestAll()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
