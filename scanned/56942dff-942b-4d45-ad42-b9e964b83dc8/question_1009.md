# Q1009: DelegateVoteRewardPool.getReward - delegate votes are excluded from totalVlMgpInVote but included in pool totals

## Question
rewards/DelegateVoteRewardPool.sol: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. With _for (any victim) and the settlement timing under attacker control and totalSupply is zero and queuedRewards holds a backlog, can an unprivileged caller sequence `getReward(address _for)` so that `userRewards[_rewardToken][account]` and `userRewardPerTokenPaid[_rewardToken][account]` no longer reconcile, violating the invariant that a vote counted in a pool total must also be counted in the denominator used to scale that total and realising Critical - Governance voting result manipulation?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: delegate votes are excluded from totalVlMgpInVote but included in pool totals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Precondition: totalSupply is zero and queuedRewards holds a backlog.
- Invariant to test: a vote counted in a pool total must also be counted in the denominator used to scale that total; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under totalSupply is zero and queuedRewards holds a backlog, then assert `userRewards[_rewardToken][account]` and `userRewardPerTokenPaid[_rewardToken][account]` end identical in both runs.
