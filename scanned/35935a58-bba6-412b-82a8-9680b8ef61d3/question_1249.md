# Q1249: DelegateVoteRewardPool.getReward - tokens beyond the first bribe token arrive unaccounted

## Question
Consider rewards/DelegateVoteRewardPool.sol, where claimAllBribes reports only rewardTokens()[0] per pool while getReward transfers every registered bribe token, so the extra tokens land on this contract without ever being queued and become unclaimable. Assuming the attacker obtains delegate-pool balance in the block before a large bribe lands, can an unprivileged attacker turn this into a divergence between `userRewards[_rewardToken][account]` and `userRewardPerTokenPaid[_rewardToken][account]` via `getReward(address _for)`, breaking the invariant that every token that arrives must be routed into the index or returned and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: tokens beyond the first bribe token arrive unaccounted)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: claimAllBribes reports only rewardTokens()[0] per pool while getReward transfers every registered bribe token, so the extra tokens land on this contract without ever being queued and become unclaimable. Precondition: the attacker obtains delegate-pool balance in the block before a large bribe lands.
- Invariant to test: every token that arrives must be routed into the index or returned; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any victim) and the settlement timing) under the attacker obtains delegate-pool balance in the block before a large bribe lands, asserting on every row that every token that arrives must be routed into the index or returned.
