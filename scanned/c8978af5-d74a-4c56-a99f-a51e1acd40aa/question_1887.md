# Q1887: DelegateVoteRewardPool.getReward - delegate votes are excluded from totalVlMgpInVote but included in pool totals

## Question
rewards/DelegateVoteRewardPool.sol: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Under protocolFee is zero so the whole reported amount is queued, is there an unprivileged sequence of `getReward(address _for)` that leaves `votingWeights[pool] and totalWeight` unreconciled with `the deltas pushed by _updateVote`, violates the invariant that a vote counted in a pool total must also be counted in the denominator used to scale that total, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: delegate votes are excluded from totalVlMgpInVote but included in pool totals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Precondition: protocolFee is zero so the whole reported amount is queued.
- Invariant to test: a vote counted in a pool total must also be counted in the denominator used to scale that total; concretely, `votingWeights[pool] and totalWeight` must stay reconciled with `the deltas pushed by _updateVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `getReward(address _for)`: constrain the setup so that protocolFee is zero so the whole reported amount is queued, fuzz the attacker inputs (_for (any victim) and the settlement timing), and assert after every call that a vote counted in a pool total must also be counted in the denominator used to scale that total.
