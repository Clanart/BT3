# Q2140: DelegateVoteRewardPool.getReward - delegate votes are excluded from totalVlMgpInVote but included in pool totals

## Question
rewards/DelegateVoteRewardPool.sol - WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Can an unprivileged attacker controlling _for (any victim) and the settlement timing, under a bribe token has a transfer hook the attacker controls, exploit this through `getReward(address _for)` to break the reconciliation between `protocolFee` and `earnedRewards[index]` and the invariant that a vote counted in a pool total must also be counted in the denominator used to scale that total, yielding Critical - Governance voting result manipulation?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: delegate votes are excluded from totalVlMgpInVote but included in pool totals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Precondition: a bribe token has a transfer hook the attacker controls.
- Invariant to test: a vote counted in a pool total must also be counted in the denominator used to scale that total; concretely, `protocolFee` must stay reconciled with `earnedRewards[index]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `getReward(address _for)` sequence atomically under a bribe token has a transfer hook the attacker controls, asserting at the end that `protocolFee` still equals `earnedRewards[index]` and the PoC's balance delta is non-positive.
