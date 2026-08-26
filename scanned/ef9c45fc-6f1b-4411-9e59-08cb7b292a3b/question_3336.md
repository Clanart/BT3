# Q3336: BaseRewardPoolV2.updateFor - early-continue skips a genuine balance change

## Question
Consider rewards/BaseRewardPoolV2.sol, where the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Assuming the attacker funds the action with a flash loan of the staking token repaid in the same transaction, can an unprivileged attacker turn this into a divergence between `totalStaked()` and `IERC20(stakingToken).balanceOf(operator)` via `updateFor(address _account)`, breaking the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker funds the action with a flash loan of the staking token repaid in the same transaction, snapshot `totalStaked()` and `IERC20(stakingToken).balanceOf(operator)`, run the attacker's `updateFor(address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
