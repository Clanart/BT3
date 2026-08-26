# Q3455: BaseRewardPoolV2.donateRewards - early-continue skips a genuine balance change

## Question
Consider rewards/BaseRewardPoolV2.sol, where the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Assuming the attacker funds the action with a flash loan of the staking token repaid in the same transaction, can an unprivileged attacker turn this into a divergence between `balanceOf(account)` and `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` via `donateRewards(uint256 _amountReward, address _rewardToken)`, breaking the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker funds the action with a flash loan of the staking token repaid in the same transaction, snapshot `balanceOf(account)` and `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, run the attacker's `donateRewards(uint256 _amountReward, address _rewardToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
