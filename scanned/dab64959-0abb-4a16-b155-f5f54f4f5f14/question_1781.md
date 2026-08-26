# Q1781: BaseRewardPool.donateRewards - early-continue skips a genuine balance change

## Question
In rewards/BaseRewardPool.sol, the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Can an unprivileged attacker reach this through `donateRewards(uint256 _amountReward, address _rewardToken)` while V1 recomputes stakingDecimals() by an external IERC20Metadata call on every accrual and its _updateFor has no early-continue, and drive `10**stakingDecimals()` out of agreement with `totalStaked()` - breaking the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: V1 recomputes stakingDecimals() by an external IERC20Metadata call on every accrual and its _updateFor has no early-continue.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange V1 recomputes stakingDecimals() by an external IERC20Metadata call on every accrual and its _updateFor has no early-continue, call `donateRewards(uint256 _amountReward, address _rewardToken)`, and assert `10**stakingDecimals()` equals `totalStaked()` and that no account can withdraw more than it put in.
