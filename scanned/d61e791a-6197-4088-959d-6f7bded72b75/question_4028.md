# Q4028: BaseRewardPoolV2.donateRewards - first-staker capture of the queued backlog

## Question
In rewards/BaseRewardPoolV2.sol, while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Can an unprivileged attacker reach this through `donateRewards(uint256 _amountReward, address _rewardToken)` while the reward token charges a transfer fee so the received balance is below the requested amount, and drive `10**stakingDecimals()` out of agreement with `totalStaked()` - breaking the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: first-staker capture of the queued backlog)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Precondition: the reward token charges a transfer fee so the received balance is below the requested amount.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block depositor; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the reward token charges a transfer fee so the received balance is below the requested amount, call `donateRewards(uint256 _amountReward, address _rewardToken)`, and assert `10**stakingDecimals()` equals `totalStaked()` and that no account can withdraw more than it put in.
