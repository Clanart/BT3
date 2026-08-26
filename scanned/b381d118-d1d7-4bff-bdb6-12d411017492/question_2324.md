# Q2324: DelegateVoteRewardPool.getReward - tokens beyond the first bribe token arrive unaccounted

## Question
In rewards/DelegateVoteRewardPool.sol, claimAllBribes reports only rewardTokens()[0] per pool while getReward transfers every registered bribe token, so the extra tokens land on this contract without ever being queued and become unclaimable. Does `getReward(address _for)` let an unprivileged caller exploit that under the delegated pool holds a dominant share of one pool's totalVoteInVlmgp, so that `protocolFee` diverges from `earnedRewards[index]`, the invariant that every token that arrives must be routed into the index or returned is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: tokens beyond the first bribe token arrive unaccounted)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: claimAllBribes reports only rewardTokens()[0] per pool while getReward transfers every registered bribe token, so the extra tokens land on this contract without ever being queued and become unclaimable. Precondition: the delegated pool holds a dominant share of one pool's totalVoteInVlmgp.
- Invariant to test: every token that arrives must be routed into the index or returned; concretely, `protocolFee` must stay reconciled with `earnedRewards[index]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the delegated pool holds a dominant share of one pool's totalVoteInVlmgp, snapshot `protocolFee` and `earnedRewards[index]`, run the attacker's `getReward(address _for)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
