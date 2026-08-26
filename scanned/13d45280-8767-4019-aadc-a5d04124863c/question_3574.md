# Q3574: vlMGPBaseRewarder.updateFor - permissionless updateFor pins a victim's index

## Question
rewards/vlMGPBaseRewarder.sol: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. With the victim address and the block at which their index is pinned under attacker control and totalStaked is zero and queuedRewards holds a backlog, can an unprivileged caller sequence `updateFor(address _account)` so that `forfeitAmount` and `rewardInfo.rewardPerTokenStored` no longer reconcile, violating the invariant that only the account or an operator acting on a real balance change may advance a user's reward index and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor pins a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: only the account or an operator acting on a real balance change may advance a user's reward index; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the block at which their index is pinned) under totalStaked is zero and queuedRewards holds a backlog, asserting on every row that only the account or an operator acting on a real balance change may advance a user's reward index.
