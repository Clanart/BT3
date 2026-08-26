# Q0575: DelegateVoteRewardPool.getReward - tokens beyond the first bribe token arrive unaccounted

## Question
In rewards/DelegateVoteRewardPool.sol, claimAllBribes reports only rewardTokens()[0] per pool while getReward transfers every registered bribe token, so the extra tokens land on this contract without ever being queued and become unclaimable. Can an unprivileged attacker reach this through `getReward(address _for)` while the pool rewarder holds less than the earned figure claimAllBribes reported, and drive `protocolFee` out of agreement with `earnedRewards[index]` - breaking the invariant that every token that arrives must be routed into the index or returned - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: tokens beyond the first bribe token arrive unaccounted)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: claimAllBribes reports only rewardTokens()[0] per pool while getReward transfers every registered bribe token, so the extra tokens land on this contract without ever being queued and become unclaimable. Precondition: the pool rewarder holds less than the earned figure claimAllBribes reported.
- Invariant to test: every token that arrives must be routed into the index or returned; concretely, `protocolFee` must stay reconciled with `earnedRewards[index]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _for)`: constrain the setup so that the pool rewarder holds less than the earned figure claimAllBribes reported, fuzz the attacker inputs (_for (any victim) and the settlement timing), and assert after every call that every token that arrives must be routed into the index or returned.
