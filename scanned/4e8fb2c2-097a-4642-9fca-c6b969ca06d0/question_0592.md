# Q0592: BaseRewardPoolV2.updateFor - permissionless updateFor snapshots a victim's index

## Question
rewards/BaseRewardPoolV2.sol: updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, is there an unprivileged sequence of `updateFor(address _account)` that leaves `userRewards[_rewardToken][account]` unreconciled with `earned(account,_rewardToken)`, violates the invariant that a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor snapshots a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, asserting at the end that `userRewards[_rewardToken][account]` still equals `earned(account,_rewardToken)` and the PoC's balance delta is non-positive.
