# Q2639: DelegateVoteRewardPool.getReward - delegate votes are excluded from totalVlMgpInVote but included in pool totals

## Question
rewards/DelegateVoteRewardPool.sol: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Under a keeper castVotes transaction that ends in harvestAll is pending in the mempool, is there an unprivileged sequence of `getReward(address _for)` that leaves `userRewards[_rewardToken][account]` unreconciled with `userRewardPerTokenPaid[_rewardToken][account]`, violates the invariant that a vote counted in a pool total must also be counted in the denominator used to scale that total, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: delegate votes are excluded from totalVlMgpInVote but included in pool totals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Precondition: a keeper castVotes transaction that ends in harvestAll is pending in the mempool.
- Invariant to test: a vote counted in a pool total must also be counted in the denominator used to scale that total; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange a keeper castVotes transaction that ends in harvestAll is pending in the mempool, call `getReward(address _for)`, and assert `userRewards[_rewardToken][account]` equals `userRewardPerTokenPaid[_rewardToken][account]` and that no account can withdraw more than it put in.
