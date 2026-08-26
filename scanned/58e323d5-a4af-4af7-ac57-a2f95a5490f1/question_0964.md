# Q0964: BaseRewardPoolV2.donateRewards - fee-on-transfer or rebasing reward token inflates the index

## Question
In rewards/BaseRewardPoolV2.sol, _provisionReward() credits the full _amountReward to historicalRewards and to the rewardPerTokenStored increment based on the requested amount rather than the balance actually received, so any reward token that delivers less than requested promises more than the pool holds. Can an unprivileged attacker reach this through `donateRewards(uint256 _amountReward, address _rewardToken)` while rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, and drive `10**stakingDecimals()` out of agreement with `totalStaked()` - breaking the invariant that the amount credited to the index must equal the balance delta actually received by the pool - for Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: fee-on-transfer or rebasing reward token inflates the index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() credits the full _amountReward to historicalRewards and to the rewardPerTokenStored increment based on the requested amount rather than the balance actually received, so any reward token that delivers less than requested promises more than the pool holds. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: the amount credited to the index must equal the balance delta actually received by the pool; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, then assert `10**stakingDecimals()` and `totalStaked()` end identical in both runs.
