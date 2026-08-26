# Q3970: vlMGPBaseRewarder.updateFor - permissionless updateFor pins a victim's index

## Question
In rewards/vlMGPBaseRewarder.sol, updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Does `updateFor(address _account)` let an unprivileged caller exploit that under the attacker locks one block before a known large settlement and unlocks one block after, so that `rewards[_rewardToken].historicalRewards` diverges from `IERC20(_rewardToken).balanceOf(address(this))`, the invariant that only the account or an operator acting on a real balance change may advance a user's reward index is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor pins a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: only the account or an operator acting on a real balance change may advance a user's reward index; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under the attacker locks one block before a known large settlement and unlocks one block after, asserting at the end that `rewards[_rewardToken].historicalRewards` still equals `IERC20(_rewardToken).balanceOf(address(this))` and the PoC's balance delta is non-positive.
