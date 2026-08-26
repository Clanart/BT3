# Q0250: BaseRewardPool.donateRewards - donateRewards rounds the increment to zero

## Question
In rewards/BaseRewardPool.sol, _provisionReward() adds (_amountReward * 10**stakingDecimals()) / totalStaked() to rewardPerTokenStored, so when totalStaked() exceeds _amountReward * 10**decimals the whole donation is pulled in by safeTransferFrom, credited to historicalRewards, and adds nothing to rewardPerTokenStored. Starting from a state where the pool has exactly one registered reward token and no queued backlog, can an unprivileged EOA use `donateRewards(uint256 _amountReward, address _rewardToken)` to leave `totalStaked()` inconsistent with `IERC20(stakingToken).balanceOf(operator)`, violating the invariant that every reward token that enters the pool must become claimable by some staker; nothing may be silently stranded and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards rounds the increment to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() adds (_amountReward * 10**stakingDecimals()) / totalStaked() to rewardPerTokenStored, so when totalStaked() exceeds _amountReward * 10**decimals the whole donation is pulled in by safeTransferFrom, credited to historicalRewards, and adds nothing to rewardPerTokenStored. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: every reward token that enters the pool must become claimable by some staker; nothing may be silently stranded; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the pool has exactly one registered reward token and no queued backlog, have the attacker run `donateRewards(uint256 _amountReward, address _rewardToken)`, then assert the victim's claimable value and the `totalStaked()` versus `IERC20(stakingToken).balanceOf(operator)` relation are unchanged by the attacker's transaction.
