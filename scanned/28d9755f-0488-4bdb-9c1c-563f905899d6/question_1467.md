# Q1467: DelegateVoteRewardPool.harvestAll - delegate votes are excluded from totalVlMgpInVote but included in pool totals

## Question
Consider rewards/DelegateVoteRewardPool.sol, where WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Assuming protocolFee is non-zero and feeCollector is set, can an unprivileged attacker turn this into a divergence between `earnedRewards returned by claimAllBribes` and `IERC20(rewardToken).balanceOf(address(this))` via `harvestAll()`, breaking the invariant that a vote counted in a pool total must also be counted in the denominator used to scale that total and producing Critical - Governance voting result manipulation?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: delegate votes are excluded from totalVlMgpInVote but included in pool totals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Precondition: protocolFee is non-zero and feeCollector is set.
- Invariant to test: a vote counted in a pool total must also be counted in the denominator used to scale that total; concretely, `earnedRewards returned by claimAllBribes` must stay reconciled with `IERC20(rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange protocolFee is non-zero and feeCollector is set, call `harvestAll()`, and assert `earnedRewards returned by claimAllBribes` equals `IERC20(rewardToken).balanceOf(address(this))` and that no account can withdraw more than it put in.
