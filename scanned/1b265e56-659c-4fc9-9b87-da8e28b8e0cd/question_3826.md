# Q3826: BaseRewardPool.updateFor - early-continue skips a genuine balance change

## Question
rewards/BaseRewardPool.sol - the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Can an unprivileged attacker controlling the victim address and the exact block in which their reward index is snapshotted, under the attacker funds the action with a flash loan of the staking token repaid in the same transaction, exploit this through `updateFor(address _account)` to break the reconciliation between `totalStaked()` and `IERC20(stakingToken).balanceOf(operator)` and the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker funds the action with a flash loan of the staking token repaid in the same transaction, snapshot `totalStaked()` and `IERC20(stakingToken).balanceOf(operator)`, run the attacker's `updateFor(address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
