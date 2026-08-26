# Q1480: BaseRewardPoolV2.donateRewards - early-continue skips a genuine balance change

## Question
In rewards/BaseRewardPoolV2.sol, the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Starting from a state where V2 caches stakingTokenDecimals at construction and both _updateFor and the updateRewards modifier early-continue when userRewardPerTokenPaid equals rewardPerTokenStored, can an unprivileged EOA use `donateRewards(uint256 _amountReward, address _rewardToken)` to leave `10**stakingDecimals()` inconsistent with `totalStaked()`, violating the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: V2 caches stakingTokenDecimals at construction and both _updateFor and the updateRewards modifier early-continue when userRewardPerTokenPaid equals rewardPerTokenStored.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange V2 caches stakingTokenDecimals at construction and both _updateFor and the updateRewards modifier early-continue when userRewardPerTokenPaid equals rewardPerTokenStored, call `donateRewards(uint256 _amountReward, address _rewardToken)`, and assert `10**stakingDecimals()` equals `totalStaked()` and that no account can withdraw more than it put in.
