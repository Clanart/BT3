# Q2071: DelegateVoteRewardPool.getReward - tokens beyond the first bribe token arrive unaccounted

## Question
In rewards/DelegateVoteRewardPool.sol, claimAllBribes reports only rewardTokens()[0] per pool while getReward transfers every registered bribe token, so the extra tokens land on this contract without ever being queued and become unclaimable. Can an unprivileged attacker reach this through `getReward(address _for)` while a bribe token has a transfer hook the attacker controls, and drive `votingWeights[pool] and totalWeight` out of agreement with `the deltas pushed by _updateVote` - breaking the invariant that every token that arrives must be routed into the index or returned - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: tokens beyond the first bribe token arrive unaccounted)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: claimAllBribes reports only rewardTokens()[0] per pool while getReward transfers every registered bribe token, so the extra tokens land on this contract without ever being queued and become unclaimable. Precondition: a bribe token has a transfer hook the attacker controls.
- Invariant to test: every token that arrives must be routed into the index or returned; concretely, `votingWeights[pool] and totalWeight` must stay reconciled with `the deltas pushed by _updateVote`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a bribe token has a transfer hook the attacker controls, have the attacker run `getReward(address _for)`, then assert the victim's claimable value and the `votingWeights[pool] and totalWeight` versus `the deltas pushed by _updateVote` relation are unchanged by the attacker's transaction.
