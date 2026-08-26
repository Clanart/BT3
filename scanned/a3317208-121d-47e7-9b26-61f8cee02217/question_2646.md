# Q2646: BaseRewardPoolV2.updateFor - early-continue skips a genuine balance change

## Question
In rewards/BaseRewardPoolV2.sol, the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Can an unprivileged attacker reach this through `updateFor(address _account)` while the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, and drive `rewards[_rewardToken].rewardPerTokenStored` out of agreement with `userRewardPerTokenPaid[_rewardToken][account]` - breaking the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, call `updateFor(address _account)`, and assert `rewards[_rewardToken].rewardPerTokenStored` equals `userRewardPerTokenPaid[_rewardToken][account]` and that no account can withdraw more than it put in.
