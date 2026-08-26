# Q2393: DelegateVoteRewardPool.getReward - delegate votes are excluded from totalVlMgpInVote but included in pool totals

## Question
rewards/DelegateVoteRewardPool.sol: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. With _for (any victim) and the settlement timing under attacker control and the delegated pool holds a dominant share of one pool's totalVoteInVlmgp, can an unprivileged caller sequence `getReward(address _for)` so that `_balances[account]` and `totalSupply` no longer reconcile, violating the invariant that a vote counted in a pool total must also be counted in the denominator used to scale that total and realising Critical - Governance voting result manipulation?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: delegate votes are excluded from totalVlMgpInVote but included in pool totals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Precondition: the delegated pool holds a dominant share of one pool's totalVoteInVlmgp.
- Invariant to test: a vote counted in a pool total must also be counted in the denominator used to scale that total; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any victim) and the settlement timing) under the delegated pool holds a dominant share of one pool's totalVoteInVlmgp, asserting on every row that a vote counted in a pool total must also be counted in the denominator used to scale that total.
