# Q4784: BaseRewardPool.donateRewards - donateRewards rounds the increment to zero

## Question
rewards/BaseRewardPool.sol: _provisionReward() adds (_amountReward * 10**stakingDecimals()) / totalStaked() to rewardPerTokenStored, so when totalStaked() exceeds _amountReward * 10**decimals the whole donation is pulled in by safeTransferFrom, credited to historicalRewards, and adds nothing to rewardPerTokenStored. With _amountReward down to one wei and which registered reward token is provisioned under attacker control and a previously registered reward token has begun reverting on transfer, can an unprivileged caller sequence `donateRewards(uint256 _amountReward, address _rewardToken)` so that `rewards[_rewardToken].queuedRewards` and `rewards[_rewardToken].rewardPerTokenStored` no longer reconcile, violating the invariant that every reward token that enters the pool must become claimable by some staker; nothing may be silently stranded and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards rounds the increment to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() adds (_amountReward * 10**stakingDecimals()) / totalStaked() to rewardPerTokenStored, so when totalStaked() exceeds _amountReward * 10**decimals the whole donation is pulled in by safeTransferFrom, credited to historicalRewards, and adds nothing to rewardPerTokenStored. Precondition: a previously registered reward token has begun reverting on transfer.
- Invariant to test: every reward token that enters the pool must become claimable by some staker; nothing may be silently stranded; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned) under a previously registered reward token has begun reverting on transfer, asserting on every row that every reward token that enters the pool must become claimable by some staker; nothing may be silently stranded.
