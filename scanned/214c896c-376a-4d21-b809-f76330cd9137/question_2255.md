# Q2255: DelegateVoteRewardPool.harvestAll - delegate votes are excluded from totalVlMgpInVote but included in pool totals

## Question
Note that in rewards/DelegateVoteRewardPool.sol, WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Can an attacker holding only tokens bought on market reach it via `harvestAll()` under the delegated pool holds a dominant share of one pool's totalVoteInVlmgp and force `protocolFee` apart from `earnedRewards[index]`, breaking the invariant that a vote counted in a pool total must also be counted in the denominator used to scale that total for Critical - Governance voting result manipulation?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: delegate votes are excluded from totalVlMgpInVote but included in pool totals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Precondition: the delegated pool holds a dominant share of one pool's totalVoteInVlmgp.
- Invariant to test: a vote counted in a pool total must also be counted in the denominator used to scale that total; concretely, `protocolFee` must stay reconciled with `earnedRewards[index]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange the delegated pool holds a dominant share of one pool's totalVoteInVlmgp, call `harvestAll()`, and assert `protocolFee` equals `earnedRewards[index]` and that no account can withdraw more than it put in.
