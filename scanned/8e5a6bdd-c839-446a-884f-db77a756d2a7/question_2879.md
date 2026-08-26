# Q2879: BaseRewardPool.getRewards - duplicate reward tokens inside one getRewards array

## Question
In rewards/BaseRewardPool.sol, getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Can an unprivileged attacker reach this through `getRewards(address _account, address _receiver, address[] _rewardTokens)` while the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, and drive `10**stakingDecimals()` out of agreement with `totalStaked()` - breaking the invariant that settling the same reward token twice in one call must be equivalent to settling it once - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: duplicate reward tokens inside one getRewards array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small.
- Invariant to test: settling the same reward token twice in one call must be equivalent to settling it once; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor) under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, asserting on every row that settling the same reward token twice in one call must be equivalent to settling it once.
