# Q4592: BaseRewardPool.donateRewards - early-continue skips a genuine balance change

## Question
In rewards/BaseRewardPool.sol, the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Starting from a state where the reward token charges a transfer fee so the received balance is below the requested amount, can an unprivileged EOA use `donateRewards(uint256 _amountReward, address _rewardToken)` to leave `rewards[_rewardToken].historicalRewards` inconsistent with `IERC20(_rewardToken).balanceOf(address(this))`, violating the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the reward token charges a transfer fee so the received balance is below the requested amount.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the reward token charges a transfer fee so the received balance is below the requested amount, have the attacker run `donateRewards(uint256 _amountReward, address _rewardToken)`, then assert the victim's claimable value and the `rewards[_rewardToken].historicalRewards` versus `IERC20(_rewardToken).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
