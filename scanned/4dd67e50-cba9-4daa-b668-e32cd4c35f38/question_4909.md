# Q4909: BaseRewardPool.getRewards - duplicate reward tokens inside one getRewards array

## Question
Note that in rewards/BaseRewardPool.sol, getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Can an attacker holding only tokens bought on market reach it via `getRewards(address _account, address _receiver, address[] _rewardTokens)` under a previously registered reward token has begun reverting on transfer and force `rewards[_rewardToken].queuedRewards` apart from `rewards[_rewardToken].rewardPerTokenStored`, breaking the invariant that settling the same reward token twice in one call must be equivalent to settling it once for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: duplicate reward tokens inside one getRewards array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Precondition: a previously registered reward token has begun reverting on transfer.
- Invariant to test: settling the same reward token twice in one call must be equivalent to settling it once; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a previously registered reward token has begun reverting on transfer, have the attacker run `getRewards(address _account, address _receiver, address[] _rewardTokens)`, then assert the victim's claimable value and the `rewards[_rewardToken].queuedRewards` versus `rewards[_rewardToken].rewardPerTokenStored` relation are unchanged by the attacker's transaction.
