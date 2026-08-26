# Q4335: BaseRewardPool.getRewards - attacker-chosen reward-token array reaches getRewards

## Question
rewards/BaseRewardPool.sol: MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Under the victim has not been settled for several epochs and holds a large userRewards balance, is there an unprivileged sequence of `getRewards(address _account, address _receiver, address[] _rewardTokens)` that leaves `userRewards[_rewardToken][account]` unreconciled with `earned(account,_rewardToken)`, violates the invariant that the set of tokens settled during a claim must not change the total value the claimer is entitled to, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token array reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Precondition: the victim has not been settled for several epochs and holds a large userRewards balance.
- Invariant to test: the set of tokens settled during a claim must not change the total value the claimer is entitled to; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getRewards(address _account, address _receiver, address[] _rewardTokens)`: constrain the setup so that the victim has not been settled for several epochs and holds a large userRewards balance, fuzz the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor), and assert after every call that the set of tokens settled during a claim must not change the total value the claimer is entitled to.
