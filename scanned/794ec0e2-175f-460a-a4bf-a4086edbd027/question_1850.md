# Q1850: BaseRewardPool.getRewards - attacker-chosen reward-token array reaches getRewards

## Question
Consider rewards/BaseRewardPool.sol, where MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Assuming V1 recomputes stakingDecimals() by an external IERC20Metadata call on every accrual and its _updateFor has no early-continue, can an unprivileged attacker turn this into a divergence between `balanceOf(account)` and `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` via `getRewards(address _account, address _receiver, address[] _rewardTokens)`, breaking the invariant that the set of tokens settled during a claim must not change the total value the claimer is entitled to and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token array reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Precondition: V1 recomputes stakingDecimals() by an external IERC20Metadata call on every accrual and its _updateFor has no early-continue.
- Invariant to test: the set of tokens settled during a claim must not change the total value the claimer is entitled to; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under V1 recomputes stakingDecimals() by an external IERC20Metadata call on every accrual and its _updateFor has no early-continue, then assert `balanceOf(account)` and `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` end identical in both runs.
