# Q4843: BaseRewardPool.donateRewards - stakingDecimals sourced from an external metadata call

## Question
rewards/BaseRewardPool.sol: the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. With _amountReward down to one wei and which registered reward token is provisioned under attacker control and a previously registered reward token has begun reverting on transfer, can an unprivileged caller sequence `donateRewards(uint256 _amountReward, address _rewardToken)` so that `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` no longer reconcile, violating the invariant that the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: stakingDecimals sourced from an external metadata call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Precondition: a previously registered reward token has begun reverting on transfer.
- Invariant to test: the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up a previously registered reward token has begun reverting on transfer, snapshot `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)`, run the attacker's `donateRewards(uint256 _amountReward, address _rewardToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
