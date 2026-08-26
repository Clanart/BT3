# Q2403: BaseRewardPoolV2.donateRewards - fee-on-transfer or rebasing reward token inflates the index

## Question
rewards/BaseRewardPoolV2.sol: _provisionReward() credits the full _amountReward to historicalRewards and to the rewardPerTokenStored increment based on the requested amount rather than the balance actually received, so any reward token that delivers less than requested promises more than the pool holds. Under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, is there an unprivileged sequence of `donateRewards(uint256 _amountReward, address _rewardToken)` that leaves `userRewards[_rewardToken][account]` unreconciled with `earned(account,_rewardToken)`, violates the invariant that the amount credited to the index must equal the balance delta actually received by the pool, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: fee-on-transfer or rebasing reward token inflates the index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() credits the full _amountReward to historicalRewards and to the rewardPerTokenStored increment based on the requested amount rather than the balance actually received, so any reward token that delivers less than requested promises more than the pool holds. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small.
- Invariant to test: the amount credited to the index must equal the balance delta actually received by the pool; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, then assert `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` end identical in both runs.
