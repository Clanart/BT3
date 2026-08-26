# Q4642: vlMGPBaseRewarder.updateFor - permissionless updateFor pins a victim's index

## Question
In rewards/vlMGPBaseRewarder.sol, updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Does `updateFor(address _account)` let an unprivileged caller exploit that under a registered reward token has begun reverting on transfer, so that `userRewards[_rewardToken][account]` diverges from `rewards[_rewardToken].rewardPerTokenStored`, the invariant that only the account or an operator acting on a real balance change may advance a user's reward index is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor pins a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Precondition: a registered reward token has begun reverting on transfer.
- Invariant to test: only the account or an operator acting on a real balance change may advance a user's reward index; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the block at which their index is pinned) under a registered reward token has begun reverting on transfer, asserting on every row that only the account or an operator acting on a real balance change may advance a user's reward index.
