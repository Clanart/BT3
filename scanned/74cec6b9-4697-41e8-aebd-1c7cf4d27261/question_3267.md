# Q3267: BaseRewardPool.getRewards - attacker-chosen reward-token array reaches getRewards

## Question
In rewards/BaseRewardPool.sol, MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Starting from a state where the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, can an unprivileged EOA use `getRewards(address _account, address _receiver, address[] _rewardTokens)` to leave `10**stakingDecimals()` inconsistent with `totalStaked()`, violating the invariant that the set of tokens settled during a claim must not change the total value the claimer is entitled to and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token array reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: the set of tokens settled during a claim must not change the total value the claimer is entitled to; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getRewards(address _account, address _receiver, address[] _rewardTokens)`: constrain the setup so that the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, fuzz the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor), and assert after every call that the set of tokens settled during a claim must not change the total value the claimer is entitled to.
