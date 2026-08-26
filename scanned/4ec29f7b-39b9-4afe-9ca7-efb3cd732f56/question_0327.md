# Q0327: DelegateVoteRewardPool.getReward - delegate votes are excluded from totalVlMgpInVote but included in pool totals

## Question
rewards/DelegateVoteRewardPool.sol: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Under the bribe contract for a voted pool registers more than one reward token, is there an unprivileged sequence of `getReward(address _for)` that leaves `protocolFee` unreconciled with `earnedRewards[index]`, violates the invariant that a vote counted in a pool total must also be counted in the denominator used to scale that total, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: delegate votes are excluded from totalVlMgpInVote but included in pool totals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Precondition: the bribe contract for a voted pool registers more than one reward token.
- Invariant to test: a vote counted in a pool total must also be counted in the denominator used to scale that total; concretely, `protocolFee` must stay reconciled with `earnedRewards[index]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish the bribe contract for a voted pool registers more than one reward token, have the attacker run `getReward(address _for)`, then assert the victim's claimable value and the `protocolFee` versus `earnedRewards[index]` relation are unchanged by the attacker's transaction.
