# Q4543: BaseRewardPool.donateRewards - first-staker capture of the queued backlog

## Question
In rewards/BaseRewardPool.sol, while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Starting from a state where the reward token charges a transfer fee so the received balance is below the requested amount, can an unprivileged EOA use `donateRewards(uint256 _amountReward, address _rewardToken)` to leave `10**stakingDecimals()` inconsistent with `totalStaked()`, violating the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: first-staker capture of the queued backlog)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Precondition: the reward token charges a transfer fee so the received balance is below the requested amount.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block depositor; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the reward token charges a transfer fee so the received balance is below the requested amount, have the attacker run `donateRewards(uint256 _amountReward, address _rewardToken)`, then assert the victim's claimable value and the `10**stakingDecimals()` versus `totalStaked()` relation are unchanged by the attacker's transaction.
