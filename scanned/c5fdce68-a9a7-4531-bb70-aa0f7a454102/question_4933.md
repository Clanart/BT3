# Q4933: mWOMSVBaseRewarder.updateFor - permissionless updateFor pins a victim's index

## Question
In rewards/mWOMSVBaseRewarder.sol, updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Can an unprivileged attacker reach this through `updateFor(address _account)` while the attacker settles the same reward token through two separate multiclaimSpec calls in one block, and drive `totalStaked()` out of agreement with `IERC20(mWOMSV).totalSupply()` - breaking the invariant that only the account or an operator acting on a real balance change may advance a user's reward index - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor pins a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Precondition: the attacker settles the same reward token through two separate multiclaimSpec calls in one block.
- Invariant to test: only the account or an operator acting on a real balance change may advance a user's reward index; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under the attacker settles the same reward token through two separate multiclaimSpec calls in one block, asserting at the end that `totalStaked()` still equals `IERC20(mWOMSV).totalSupply()` and the PoC's balance delta is non-positive.
