# Q0234: DelegateVoteRewardPool.getReward - tokens beyond the first bribe token arrive unaccounted

## Question
In rewards/DelegateVoteRewardPool.sol, claimAllBribes reports only rewardTokens()[0] per pool while getReward transfers every registered bribe token, so the extra tokens land on this contract without ever being queued and become unclaimable. Starting from a state where the bribe contract for a voted pool registers more than one reward token, can an unprivileged EOA use `getReward(address _for)` to leave `votingWeights[pool] and totalWeight` inconsistent with `the deltas pushed by _updateVote`, violating the invariant that every token that arrives must be routed into the index or returned and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: tokens beyond the first bribe token arrive unaccounted)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: claimAllBribes reports only rewardTokens()[0] per pool while getReward transfers every registered bribe token, so the extra tokens land on this contract without ever being queued and become unclaimable. Precondition: the bribe contract for a voted pool registers more than one reward token.
- Invariant to test: every token that arrives must be routed into the index or returned; concretely, `votingWeights[pool] and totalWeight` must stay reconciled with `the deltas pushed by _updateVote`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the bribe contract for a voted pool registers more than one reward token, then assert `votingWeights[pool] and totalWeight` and `the deltas pushed by _updateVote` end identical in both runs.
