# Q4478: BaseRewardPool.updateFor - early-continue skips a genuine balance change

## Question
In rewards/BaseRewardPool.sol, the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Can an unprivileged attacker reach this through `updateFor(address _account)` while the reward token charges a transfer fee so the received balance is below the requested amount, and drive `rewards[_rewardToken].queuedRewards` out of agreement with `rewards[_rewardToken].rewardPerTokenStored` - breaking the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the reward token charges a transfer fee so the received balance is below the requested amount.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the reward token charges a transfer fee so the received balance is below the requested amount, then assert `rewards[_rewardToken].queuedRewards` and `rewards[_rewardToken].rewardPerTokenStored` end identical in both runs.
