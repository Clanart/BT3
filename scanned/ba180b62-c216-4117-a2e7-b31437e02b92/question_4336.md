# Q4336: BaseRewardPoolV2.donateRewards - fee-on-transfer or rebasing reward token inflates the index

## Question
rewards/BaseRewardPoolV2.sol - _provisionReward() credits the full _amountReward to historicalRewards and to the rewardPerTokenStored increment based on the requested amount rather than the balance actually received, so any reward token that delivers less than requested promises more than the pool holds. Can an unprivileged attacker controlling _amountReward down to one wei and which registered reward token is provisioned, under a previously registered reward token has begun reverting on transfer, exploit this through `donateRewards(uint256 _amountReward, address _rewardToken)` to break the reconciliation between `rewardTokens.length` and `isRewardToken[_rewardToken]` and the invariant that the amount credited to the index must equal the balance delta actually received by the pool, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: fee-on-transfer or rebasing reward token inflates the index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() credits the full _amountReward to historicalRewards and to the rewardPerTokenStored increment based on the requested amount rather than the balance actually received, so any reward token that delivers less than requested promises more than the pool holds. Precondition: a previously registered reward token has begun reverting on transfer.
- Invariant to test: the amount credited to the index must equal the balance delta actually received by the pool; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a previously registered reward token has begun reverting on transfer, then assert `rewardTokens.length` and `isRewardToken[_rewardToken]` end identical in both runs.
