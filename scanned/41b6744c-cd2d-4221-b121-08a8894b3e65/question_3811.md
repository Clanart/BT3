# Q3811: BaseRewardPoolV2.getRewards - attacker-chosen reward-token array reaches getRewards

## Question
In rewards/BaseRewardPoolV2.sol, MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Starting from a state where the victim has not been settled for several epochs and holds a large userRewards balance, can an unprivileged EOA use `getRewards(address _account, address _receiver, address[] _rewardTokens)` to leave `userRewards[_rewardToken][account]` inconsistent with `earned(account,_rewardToken)`, violating the invariant that the set of tokens settled during a claim must not change the total value the claimer is entitled to and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token array reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Precondition: the victim has not been settled for several epochs and holds a large userRewards balance.
- Invariant to test: the set of tokens settled during a claim must not change the total value the claimer is entitled to; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the victim has not been settled for several epochs and holds a large userRewards balance, then assert `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` end identical in both runs.
