# Q2765: BaseRewardPool.donateRewards - stakingDecimals sourced from an external metadata call

## Question
In rewards/BaseRewardPool.sol, the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Starting from a state where the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, can an unprivileged EOA use `donateRewards(uint256 _amountReward, address _rewardToken)` to leave `balanceOf(account)` inconsistent with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, violating the invariant that the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: stakingDecimals sourced from an external metadata call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small.
- Invariant to test: the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken)`: constrain the setup so that the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, fuzz the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned), and assert after every call that the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual.
