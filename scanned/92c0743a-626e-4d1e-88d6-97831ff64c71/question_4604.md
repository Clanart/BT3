# Q4604: BaseRewardPool.donateRewards - fee-on-transfer or rebasing reward token inflates the index

## Question
In rewards/BaseRewardPool.sol, _provisionReward() credits the full _amountReward to historicalRewards and to the rewardPerTokenStored increment based on the requested amount rather than the balance actually received, so any reward token that delivers less than requested promises more than the pool holds. Can an unprivileged attacker reach this through `donateRewards(uint256 _amountReward, address _rewardToken)` while the reward token charges a transfer fee so the received balance is below the requested amount, and drive `10**stakingDecimals()` out of agreement with `totalStaked()` - breaking the invariant that the amount credited to the index must equal the balance delta actually received by the pool - for Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: fee-on-transfer or rebasing reward token inflates the index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() credits the full _amountReward to historicalRewards and to the rewardPerTokenStored increment based on the requested amount rather than the balance actually received, so any reward token that delivers less than requested promises more than the pool holds. Precondition: the reward token charges a transfer fee so the received balance is below the requested amount.
- Invariant to test: the amount credited to the index must equal the balance delta actually received by the pool; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `donateRewards(uint256 _amountReward, address _rewardToken)` sequence atomically under the reward token charges a transfer fee so the received balance is below the requested amount, asserting at the end that `10**stakingDecimals()` still equals `totalStaked()` and the PoC's balance delta is non-positive.
