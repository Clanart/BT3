# Q0813: mWOMSVBaseRewarder.updateFor - permissionless updateFor pins a victim's index

## Question
In rewards/mWOMSVBaseRewarder.sol, updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Can an unprivileged attacker reach this through `updateFor(address _account)` while the account's slot matured recently so the percent has only just begun to decay, and drive `rewards[_rewardToken].historicalRewards` out of agreement with `IERC20(_rewardToken).balanceOf(address(this))` - breaking the invariant that only the account or an operator acting on a real balance change may advance a user's reward index - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor pins a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: only the account or an operator acting on a real balance change may advance a user's reward index; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the account's slot matured recently so the percent has only just begun to decay, have the attacker run `updateFor(address _account)`, then assert the victim's claimable value and the `rewards[_rewardToken].historicalRewards` versus `IERC20(_rewardToken).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
