# Q2625: BaseRewardPool.updateFor - early-continue skips a genuine balance change

## Question
In rewards/BaseRewardPool.sol, the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Starting from a state where the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, can an unprivileged EOA use `updateFor(address _account)` to leave `rewardTokens.length` inconsistent with `isRewardToken[_rewardToken]`, violating the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, snapshot `rewardTokens.length` and `isRewardToken[_rewardToken]`, run the attacker's `updateFor(address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
