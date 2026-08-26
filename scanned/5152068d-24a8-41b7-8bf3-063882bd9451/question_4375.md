# Q4375: BaseRewardPoolV2.getRewards - duplicate reward tokens inside one getRewards array

## Question
In rewards/BaseRewardPoolV2.sol, getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Can an unprivileged attacker reach this through `getRewards(address _account, address _receiver, address[] _rewardTokens)` while a previously registered reward token has begun reverting on transfer, and drive `rewards[_rewardToken].queuedRewards` out of agreement with `rewards[_rewardToken].rewardPerTokenStored` - breaking the invariant that settling the same reward token twice in one call must be equivalent to settling it once - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: duplicate reward tokens inside one getRewards array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Precondition: a previously registered reward token has begun reverting on transfer.
- Invariant to test: settling the same reward token twice in one call must be equivalent to settling it once; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a previously registered reward token has begun reverting on transfer, then assert `rewards[_rewardToken].queuedRewards` and `rewards[_rewardToken].rewardPerTokenStored` end identical in both runs.
