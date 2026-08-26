# Q2665: BaseRewardPool.donateRewards - donateRewards rounds the increment to zero

## Question
Consider rewards/BaseRewardPool.sol, where _provisionReward() adds (_amountReward * 10**stakingDecimals()) / totalStaked() to rewardPerTokenStored, so when totalStaked() exceeds _amountReward * 10**decimals the whole donation is pulled in by safeTransferFrom, credited to historicalRewards, and adds nothing to rewardPerTokenStored. Assuming the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, can an unprivileged attacker turn this into a divergence between `10**stakingDecimals()` and `totalStaked()` via `donateRewards(uint256 _amountReward, address _rewardToken)`, breaking the invariant that every reward token that enters the pool must become claimable by some staker; nothing may be silently stranded and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards rounds the increment to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() adds (_amountReward * 10**stakingDecimals()) / totalStaked() to rewardPerTokenStored, so when totalStaked() exceeds _amountReward * 10**decimals the whole donation is pulled in by safeTransferFrom, credited to historicalRewards, and adds nothing to rewardPerTokenStored. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small.
- Invariant to test: every reward token that enters the pool must become claimable by some staker; nothing may be silently stranded; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken)`: constrain the setup so that the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, fuzz the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned), and assert after every call that every reward token that enters the pool must become claimable by some staker; nothing may be silently stranded.
