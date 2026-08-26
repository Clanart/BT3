# Q0468: BaseRewardPoolV2.getRewards - duplicate reward tokens inside one getRewards array

## Question
In rewards/BaseRewardPoolV2.sol, getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Does `getRewards(address _account, address _receiver, address[] _rewardTokens)` let an unprivileged caller exploit that under the pool has exactly one registered reward token and no queued backlog, so that `totalStaked()` diverges from `IERC20(stakingToken).balanceOf(operator)`, the invariant that settling the same reward token twice in one call must be equivalent to settling it once is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: duplicate reward tokens inside one getRewards array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: settling the same reward token twice in one call must be equivalent to settling it once; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor) under the pool has exactly one registered reward token and no queued backlog, asserting on every row that settling the same reward token twice in one call must be equivalent to settling it once.
