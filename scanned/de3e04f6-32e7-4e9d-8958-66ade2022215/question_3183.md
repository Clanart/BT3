# Q3183: BaseRewardPoolV2.getRewards - attacker-chosen reward-token array reaches getRewards

## Question
rewards/BaseRewardPoolV2.sol: MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Under a reward-manager queueNewRewards transaction is pending in the mempool, is there an unprivileged sequence of `getRewards(address _account, address _receiver, address[] _rewardTokens)` that leaves `rewardTokens.length` unreconciled with `isRewardToken[_rewardToken]`, violates the invariant that the set of tokens settled during a claim must not change the total value the claimer is entitled to, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token array reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: the set of tokens settled during a claim must not change the total value the claimer is entitled to; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a reward-manager queueNewRewards transaction is pending in the mempool, then assert `rewardTokens.length` and `isRewardToken[_rewardToken]` end identical in both runs.
