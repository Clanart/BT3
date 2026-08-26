# Q3352: BaseRewardPool.updateFor - permissionless updateFor snapshots a victim's index

## Question
rewards/BaseRewardPool.sol: updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Under a reward-manager queueNewRewards transaction is pending in the mempool, is there an unprivileged sequence of `updateFor(address _account)` that leaves `10**stakingDecimals()` unreconciled with `totalStaked()`, violates the invariant that a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor snapshots a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under a reward-manager queueNewRewards transaction is pending in the mempool, asserting at the end that `10**stakingDecimals()` still equals `totalStaked()` and the PoC's balance delta is non-positive.
