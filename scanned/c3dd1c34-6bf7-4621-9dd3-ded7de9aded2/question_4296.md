# Q4296: BaseRewardPool.donateRewards - early-continue skips a genuine balance change

## Question
In rewards/BaseRewardPool.sol, the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Starting from a state where the victim has not been settled for several epochs and holds a large userRewards balance, can an unprivileged EOA use `donateRewards(uint256 _amountReward, address _rewardToken)` to leave `rewards[_rewardToken].queuedRewards` inconsistent with `rewards[_rewardToken].rewardPerTokenStored`, violating the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the victim has not been settled for several epochs and holds a large userRewards balance.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken)`: constrain the setup so that the victim has not been settled for several epochs and holds a large userRewards balance, fuzz the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned), and assert after every call that userRewards must capture every balance-weighted segment, including segments where the global index did not move.
