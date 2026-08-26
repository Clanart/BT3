# Q1684: BaseRewardPool.donateRewards - first-staker capture of the queued backlog

## Question
In rewards/BaseRewardPool.sol, while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Can an unprivileged attacker reach this through `donateRewards(uint256 _amountReward, address _rewardToken)` while V1 recomputes stakingDecimals() by an external IERC20Metadata call on every accrual and its _updateFor has no early-continue, and drive `rewardTokens.length` out of agreement with `isRewardToken[_rewardToken]` - breaking the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: first-staker capture of the queued backlog)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Precondition: V1 recomputes stakingDecimals() by an external IERC20Metadata call on every accrual and its _updateFor has no early-continue.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block depositor; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken)`: constrain the setup so that V1 recomputes stakingDecimals() by an external IERC20Metadata call on every accrual and its _updateFor has no early-continue, fuzz the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned), and assert after every call that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor.
