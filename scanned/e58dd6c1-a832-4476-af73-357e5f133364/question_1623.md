# Q1623: DelegateVoteRewardPool.getReward - delegate votes are excluded from totalVlMgpInVote but included in pool totals

## Question
rewards/DelegateVoteRewardPool.sol: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Under protocolFee is non-zero and feeCollector is set, is there an unprivileged sequence of `getReward(address _for)` that leaves `rewards[_rewardToken].rewardPerTokenStored` unreconciled with `totalSupply of the delegate pool`, violates the invariant that a vote counted in a pool total must also be counted in the denominator used to scale that total, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: delegate votes are excluded from totalVlMgpInVote but included in pool totals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Precondition: protocolFee is non-zero and feeCollector is set.
- Invariant to test: a vote counted in a pool total must also be counted in the denominator used to scale that total; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `totalSupply of the delegate pool`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under protocolFee is non-zero and feeCollector is set, then assert `rewards[_rewardToken].rewardPerTokenStored` and `totalSupply of the delegate pool` end identical in both runs.
