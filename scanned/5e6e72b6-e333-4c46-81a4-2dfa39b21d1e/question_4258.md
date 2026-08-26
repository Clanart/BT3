# Q4258: BaseRewardPoolV2.donateRewards - donateRewards rounds the increment to zero

## Question
In rewards/BaseRewardPoolV2.sol, _provisionReward() adds (_amountReward * 10**stakingDecimals()) / totalStaked() to rewardPerTokenStored, so when totalStaked() exceeds _amountReward * 10**decimals the whole donation is pulled in by safeTransferFrom, credited to historicalRewards, and adds nothing to rewardPerTokenStored. Starting from a state where a previously registered reward token has begun reverting on transfer, can an unprivileged EOA use `donateRewards(uint256 _amountReward, address _rewardToken)` to leave `rewards[_rewardToken].queuedRewards` inconsistent with `rewards[_rewardToken].rewardPerTokenStored`, violating the invariant that every reward token that enters the pool must become claimable by some staker; nothing may be silently stranded and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards rounds the increment to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() adds (_amountReward * 10**stakingDecimals()) / totalStaked() to rewardPerTokenStored, so when totalStaked() exceeds _amountReward * 10**decimals the whole donation is pulled in by safeTransferFrom, credited to historicalRewards, and adds nothing to rewardPerTokenStored. Precondition: a previously registered reward token has begun reverting on transfer.
- Invariant to test: every reward token that enters the pool must become claimable by some staker; nothing may be silently stranded; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a previously registered reward token has begun reverting on transfer, have the attacker run `donateRewards(uint256 _amountReward, address _rewardToken)`, then assert the victim's claimable value and the `rewards[_rewardToken].queuedRewards` versus `rewards[_rewardToken].rewardPerTokenStored` relation are unchanged by the attacker's transaction.
