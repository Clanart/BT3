# Q0591: BaseRewardPool.getRewards - duplicate reward tokens inside one getRewards array

## Question
In rewards/BaseRewardPool.sol, getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Can an unprivileged attacker reach this through `getRewards(address _account, address _receiver, address[] _rewardTokens)` while the pool has exactly one registered reward token and no queued backlog, and drive `totalStaked()` out of agreement with `IERC20(stakingToken).balanceOf(operator)` - breaking the invariant that settling the same reward token twice in one call must be equivalent to settling it once - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: duplicate reward tokens inside one getRewards array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: settling the same reward token twice in one call must be equivalent to settling it once; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool has exactly one registered reward token and no queued backlog, then assert `totalStaked()` and `IERC20(stakingToken).balanceOf(operator)` end identical in both runs.
