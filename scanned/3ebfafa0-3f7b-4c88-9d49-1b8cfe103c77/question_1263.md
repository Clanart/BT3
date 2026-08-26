# Q1263: BaseRewardPool.getRewards - attacker-chosen reward-token array reaches getRewards

## Question
rewards/BaseRewardPool.sol: MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, is there an unprivileged sequence of `getRewards(address _account, address _receiver, address[] _rewardTokens)` that leaves `totalStaked()` unreconciled with `IERC20(stakingToken).balanceOf(operator)`, violates the invariant that the set of tokens settled during a claim must not change the total value the claimer is entitled to, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token array reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: the set of tokens settled during a claim must not change the total value the claimer is entitled to; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, then assert `totalStaked()` and `IERC20(stakingToken).balanceOf(operator)` end identical in both runs.
