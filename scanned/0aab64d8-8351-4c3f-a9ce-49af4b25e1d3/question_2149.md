# Q2149: BaseRewardPool.donateRewards - donateRewards rounds the increment to zero

## Question
Note that in rewards/BaseRewardPool.sol, _provisionReward() adds (_amountReward * 10**stakingDecimals()) / totalStaked() to rewardPerTokenStored, so when totalStaked() exceeds _amountReward * 10**decimals the whole donation is pulled in by safeTransferFrom, credited to historicalRewards, and adds nothing to rewardPerTokenStored. Can an attacker holding only tokens bought on market reach it via `donateRewards(uint256 _amountReward, address _rewardToken)` under the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18 and force `rewards[_rewardToken].historicalRewards` apart from `IERC20(_rewardToken).balanceOf(address(this))`, breaking the invariant that every reward token that enters the pool must become claimable by some staker; nothing may be silently stranded for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards rounds the increment to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() adds (_amountReward * 10**stakingDecimals()) / totalStaked() to rewardPerTokenStored, so when totalStaked() exceeds _amountReward * 10**decimals the whole donation is pulled in by safeTransferFrom, credited to historicalRewards, and adds nothing to rewardPerTokenStored. Precondition: the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18.
- Invariant to test: every reward token that enters the pool must become claimable by some staker; nothing may be silently stranded; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, then assert `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` end identical in both runs.
