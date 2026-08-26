# Q3097: BaseRewardPool.donateRewards - donateRewards rounds the increment to zero

## Question
Consider rewards/BaseRewardPool.sol, where _provisionReward() adds (_amountReward * 10**stakingDecimals()) / totalStaked() to rewardPerTokenStored, so when totalStaked() exceeds _amountReward * 10**decimals the whole donation is pulled in by safeTransferFrom, credited to historicalRewards, and adds nothing to rewardPerTokenStored. Assuming the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, can an unprivileged attacker turn this into a divergence between `rewardTokens.length` and `isRewardToken[_rewardToken]` via `donateRewards(uint256 _amountReward, address _rewardToken)`, breaking the invariant that every reward token that enters the pool must become claimable by some staker; nothing may be silently stranded and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards rounds the increment to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() adds (_amountReward * 10**stakingDecimals()) / totalStaked() to rewardPerTokenStored, so when totalStaked() exceeds _amountReward * 10**decimals the whole donation is pulled in by safeTransferFrom, credited to historicalRewards, and adds nothing to rewardPerTokenStored. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: every reward token that enters the pool must become claimable by some staker; nothing may be silently stranded; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, call `donateRewards(uint256 _amountReward, address _rewardToken)`, and assert `rewardTokens.length` equals `isRewardToken[_rewardToken]` and that no account can withdraw more than it put in.
