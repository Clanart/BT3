# Q2937: BaseRewardPoolV2.updateFor - permissionless updateFor snapshots a victim's index

## Question
In rewards/BaseRewardPoolV2.sol, updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Can an unprivileged attacker reach this through `updateFor(address _account)` while a reward-manager queueNewRewards transaction is pending in the mempool, and drive `10**stakingDecimals()` out of agreement with `totalStaked()` - breaking the invariant that a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor snapshots a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that a reward-manager queueNewRewards transaction is pending in the mempool, fuzz the attacker inputs (the victim address and the exact block in which their reward index is snapshotted), and assert after every call that a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change.
