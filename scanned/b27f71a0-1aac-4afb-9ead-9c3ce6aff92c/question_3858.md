# Q3858: BaseRewardPool.donateRewards - donateRewards rounds the increment to zero

## Question
In rewards/BaseRewardPool.sol, _provisionReward() adds (_amountReward * 10**stakingDecimals()) / totalStaked() to rewardPerTokenStored, so when totalStaked() exceeds _amountReward * 10**decimals the whole donation is pulled in by safeTransferFrom, credited to historicalRewards, and adds nothing to rewardPerTokenStored. Starting from a state where the attacker funds the action with a flash loan of the staking token repaid in the same transaction, can an unprivileged EOA use `donateRewards(uint256 _amountReward, address _rewardToken)` to leave `userRewards[_rewardToken][account]` inconsistent with `earned(account,_rewardToken)`, violating the invariant that every reward token that enters the pool must become claimable by some staker; nothing may be silently stranded and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards rounds the increment to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() adds (_amountReward * 10**stakingDecimals()) / totalStaked() to rewardPerTokenStored, so when totalStaked() exceeds _amountReward * 10**decimals the whole donation is pulled in by safeTransferFrom, credited to historicalRewards, and adds nothing to rewardPerTokenStored. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: every reward token that enters the pool must become claimable by some staker; nothing may be silently stranded; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker funds the action with a flash loan of the staking token repaid in the same transaction, call `donateRewards(uint256 _amountReward, address _rewardToken)`, and assert `userRewards[_rewardToken][account]` equals `earned(account,_rewardToken)` and that no account can withdraw more than it put in.
