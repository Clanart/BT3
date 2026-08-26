# Q4568: BaseRewardPool.donateRewards - stakingDecimals sourced from an external metadata call

## Question
Note that in rewards/BaseRewardPool.sol, the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Can an attacker holding only tokens bought on market reach it via `donateRewards(uint256 _amountReward, address _rewardToken)` under the reward token charges a transfer fee so the received balance is below the requested amount and force `rewards[_rewardToken].rewardPerTokenStored` apart from `userRewardPerTokenPaid[_rewardToken][account]`, breaking the invariant that the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual for Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: stakingDecimals sourced from an external metadata call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Precondition: the reward token charges a transfer fee so the received balance is below the requested amount.
- Invariant to test: the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `donateRewards(uint256 _amountReward, address _rewardToken)` sequence atomically under the reward token charges a transfer fee so the received balance is below the requested amount, asserting at the end that `rewards[_rewardToken].rewardPerTokenStored` still equals `userRewardPerTokenPaid[_rewardToken][account]` and the PoC's balance delta is non-positive.
