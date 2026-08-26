# Q0560: BaseRewardPool.getRewards - attacker-chosen reward-token array reaches getRewards

## Question
In rewards/BaseRewardPool.sol, MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Can an unprivileged attacker reach this through `getRewards(address _account, address _receiver, address[] _rewardTokens)` while the pool has exactly one registered reward token and no queued backlog, and drive `userRewards[_rewardToken][account]` out of agreement with `earned(account,_rewardToken)` - breaking the invariant that the set of tokens settled during a claim must not change the total value the claimer is entitled to - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token array reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: the set of tokens settled during a claim must not change the total value the claimer is entitled to; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor) under the pool has exactly one registered reward token and no queued backlog, asserting on every row that the set of tokens settled during a claim must not change the total value the claimer is entitled to.
