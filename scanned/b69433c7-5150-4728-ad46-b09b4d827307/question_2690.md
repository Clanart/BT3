# Q2690: mWOMSVBaseRewarder.updateFor - permissionless updateFor pins a victim's index

## Question
In rewards/mWOMSVBaseRewarder.sol, updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Does `updateFor(address _account)` let an unprivileged caller exploit that under a large MGP distribution has just been queued and no account has settled yet, so that `totalStaked()` diverges from `IERC20(mWOMSV).totalSupply()`, the invariant that only the account or an operator acting on a real balance change may advance a user's reward index is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor pins a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: only the account or an operator acting on a real balance change may advance a user's reward index; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that a large MGP distribution has just been queued and no account has settled yet, fuzz the attacker inputs (the victim address and the block at which their index is pinned), and assert after every call that only the account or an operator acting on a real balance change may advance a user's reward index.
