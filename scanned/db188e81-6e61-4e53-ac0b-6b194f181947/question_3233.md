# Q3233: BaseRewardPool.donateRewards - fee-on-transfer or rebasing reward token inflates the index

## Question
In rewards/BaseRewardPool.sol, _provisionReward() credits the full _amountReward to historicalRewards and to the rewardPerTokenStored increment based on the requested amount rather than the balance actually received, so any reward token that delivers less than requested promises more than the pool holds. Does `donateRewards(uint256 _amountReward, address _rewardToken)` let an unprivileged caller exploit that under the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, so that `totalStaked()` diverges from `IERC20(stakingToken).balanceOf(operator)`, the invariant that the amount credited to the index must equal the balance delta actually received by the pool is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: fee-on-transfer or rebasing reward token inflates the index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() credits the full _amountReward to historicalRewards and to the rewardPerTokenStored increment based on the requested amount rather than the balance actually received, so any reward token that delivers less than requested promises more than the pool holds. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: the amount credited to the index must equal the balance delta actually received by the pool; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, call `donateRewards(uint256 _amountReward, address _rewardToken)`, and assert `totalStaked()` equals `IERC20(stakingToken).balanceOf(operator)` and that no account can withdraw more than it put in.
