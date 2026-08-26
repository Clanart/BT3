# Q2861: BaseRewardPoolV2.getRewards - duplicate reward tokens inside one getRewards array

## Question
Note that in rewards/BaseRewardPoolV2.sol, getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Can an attacker holding only tokens bought on market reach it via `getRewards(address _account, address _receiver, address[] _rewardTokens)` under the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer and force `rewardTokens.length` apart from `isRewardToken[_rewardToken]`, breaking the invariant that settling the same reward token twice in one call must be equivalent to settling it once for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: duplicate reward tokens inside one getRewards array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: settling the same reward token twice in one call must be equivalent to settling it once; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, then assert `rewardTokens.length` and `isRewardToken[_rewardToken]` end identical in both runs.
