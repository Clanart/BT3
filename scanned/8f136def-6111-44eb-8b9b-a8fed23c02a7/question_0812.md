# Q0812: vlMGPBaseRewarder.updateFor - permissionless updateFor pins a victim's index

## Question
rewards/vlMGPBaseRewarder.sol: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Under the account's slot matured recently so the percent has only just begun to decay, is there an unprivileged sequence of `updateFor(address _account)` that leaves `rewards[_rewardToken].historicalRewards` unreconciled with `IERC20(_rewardToken).balanceOf(address(this))`, violates the invariant that only the account or an operator acting on a real balance change may advance a user's reward index, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor pins a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: only the account or an operator acting on a real balance change may advance a user's reward index; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under the account's slot matured recently so the percent has only just begun to decay, asserting at the end that `rewards[_rewardToken].historicalRewards` still equals `IERC20(_rewardToken).balanceOf(address(this))` and the PoC's balance delta is non-positive.
