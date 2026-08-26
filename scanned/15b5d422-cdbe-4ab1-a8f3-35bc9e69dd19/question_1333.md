# Q1333: DelegateVoteRewardPool.getReward - delegate votes are excluded from totalVlMgpInVote but included in pool totals

## Question
In rewards/DelegateVoteRewardPool.sol, WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Does `getReward(address _for)` let an unprivileged caller exploit that under the attacker obtains delegate-pool balance in the block before a large bribe lands, so that `earnedRewards returned by claimAllBribes` diverges from `IERC20(rewardToken).balanceOf(address(this))`, the invariant that a vote counted in a pool total must also be counted in the denominator used to scale that total is broken, and the result is Critical - Governance voting result manipulation?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: delegate votes are excluded from totalVlMgpInVote but included in pool totals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Precondition: the attacker obtains delegate-pool balance in the block before a large bribe lands.
- Invariant to test: a vote counted in a pool total must also be counted in the denominator used to scale that total; concretely, `earnedRewards returned by claimAllBribes` must stay reconciled with `IERC20(rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any victim) and the settlement timing) under the attacker obtains delegate-pool balance in the block before a large bribe lands, asserting on every row that a vote counted in a pool total must also be counted in the denominator used to scale that total.
