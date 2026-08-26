# Q4270: BaseRewardPool.donateRewards - stakingDecimals sourced from an external metadata call

## Question
rewards/BaseRewardPool.sol - the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Can an unprivileged attacker controlling _amountReward down to one wei and which registered reward token is provisioned, under the victim has not been settled for several epochs and holds a large userRewards balance, exploit this through `donateRewards(uint256 _amountReward, address _rewardToken)` to break the reconciliation between `rewardTokens.length` and `isRewardToken[_rewardToken]` and the invariant that the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: stakingDecimals sourced from an external metadata call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Precondition: the victim has not been settled for several epochs and holds a large userRewards balance.
- Invariant to test: the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned) under the victim has not been settled for several epochs and holds a large userRewards balance, asserting on every row that the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual.
