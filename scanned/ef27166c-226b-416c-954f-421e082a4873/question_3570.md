# Q3570: BaseRewardPool.donateRewards - stakingDecimals sourced from an external metadata call

## Question
In rewards/BaseRewardPool.sol, the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Can an unprivileged attacker reach this through `donateRewards(uint256 _amountReward, address _rewardToken)` while a reward-manager queueNewRewards transaction is pending in the mempool, and drive `rewards[_rewardToken].historicalRewards` out of agreement with `IERC20(_rewardToken).balanceOf(address(this))` - breaking the invariant that the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual - for Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: stakingDecimals sourced from an external metadata call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `donateRewards(uint256 _amountReward, address _rewardToken)` sequence atomically under a reward-manager queueNewRewards transaction is pending in the mempool, asserting at the end that `rewards[_rewardToken].historicalRewards` still equals `IERC20(_rewardToken).balanceOf(address(this))` and the PoC's balance delta is non-positive.
