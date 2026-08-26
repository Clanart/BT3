# Q4932: vlMGPBaseRewarder.updateFor - permissionless updateFor pins a victim's index

## Question
rewards/vlMGPBaseRewarder.sol: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Under the attacker settles the same reward token through two separate multiclaimSpec calls in one block, is there an unprivileged sequence of `updateFor(address _account)` that leaves `totalStaked()` unreconciled with `IERC20(vlMGP).totalSupply()`, violates the invariant that only the account or an operator acting on a real balance change may advance a user's reward index, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor pins a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Precondition: the attacker settles the same reward token through two separate multiclaimSpec calls in one block.
- Invariant to test: only the account or an operator acting on a real balance change may advance a user's reward index; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker settles the same reward token through two separate multiclaimSpec calls in one block, then assert `totalStaked()` and `IERC20(vlMGP).totalSupply()` end identical in both runs.
