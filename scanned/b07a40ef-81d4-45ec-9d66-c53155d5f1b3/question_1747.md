# Q1747: DelegateVoteRewardPool.harvestAll - delegate votes are excluded from totalVlMgpInVote but included in pool totals

## Question
Consider rewards/DelegateVoteRewardPool.sol, where WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Assuming protocolFee is zero so the whole reported amount is queued, can an unprivileged attacker turn this into a divergence between `rewards[_rewardToken].rewardPerTokenStored` and `totalSupply of the delegate pool` via `harvestAll()`, breaking the invariant that a vote counted in a pool total must also be counted in the denominator used to scale that total and producing Critical - Governance voting result manipulation?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: delegate votes are excluded from totalVlMgpInVote but included in pool totals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: WombatBribeManager.vote() skips the global counters for the delegatedPool address while still adding to poolInfos[lp].totalVoteInVlmgp, so every cast scales the delegate's weight against a denominator that never counted it. Precondition: protocolFee is zero so the whole reported amount is queued.
- Invariant to test: a vote counted in a pool total must also be counted in the denominator used to scale that total; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `totalSupply of the delegate pool`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `harvestAll()`: constrain the setup so that protocolFee is zero so the whole reported amount is queued, fuzz the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone), and assert after every call that a vote counted in a pool total must also be counted in the denominator used to scale that total.
