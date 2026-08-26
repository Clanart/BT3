# Q3650: BaseRewardPool.getRewards - attacker-chosen reward-token array reaches getRewards

## Question
Consider rewards/BaseRewardPool.sol, where MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Assuming a reward-manager queueNewRewards transaction is pending in the mempool, can an unprivileged attacker turn this into a divergence between `rewardTokens.length` and `isRewardToken[_rewardToken]` via `getRewards(address _account, address _receiver, address[] _rewardTokens)`, breaking the invariant that the set of tokens settled during a claim must not change the total value the claimer is entitled to and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token array reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: the set of tokens settled during a claim must not change the total value the claimer is entitled to; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a reward-manager queueNewRewards transaction is pending in the mempool, call `getRewards(address _account, address _receiver, address[] _rewardTokens)`, and assert `rewardTokens.length` equals `isRewardToken[_rewardToken]` and that no account can withdraw more than it put in.
