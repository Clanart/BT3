# Q1026: BaseRewardPoolV2.getRewards - attacker-chosen reward-token array reaches getRewards

## Question
Consider rewards/BaseRewardPoolV2.sol, where MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Assuming rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, can an unprivileged attacker turn this into a divergence between `totalStaked()` and `IERC20(stakingToken).balanceOf(operator)` via `getRewards(address _account, address _receiver, address[] _rewardTokens)`, breaking the invariant that the set of tokens settled during a claim must not change the total value the claimer is entitled to and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token array reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: the set of tokens settled during a claim must not change the total value the claimer is entitled to; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence atomically under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, asserting at the end that `totalStaked()` still equals `IERC20(stakingToken).balanceOf(operator)` and the PoC's balance delta is non-positive.
