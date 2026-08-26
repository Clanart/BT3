# Q3730: BaseRewardPool.updateFor - permissionless updateFor snapshots a victim's index

## Question
rewards/BaseRewardPool.sol: updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. With the victim address and the exact block in which their reward index is snapshotted under attacker control and the attacker funds the action with a flash loan of the staking token repaid in the same transaction, can an unprivileged caller sequence `updateFor(address _account)` so that `rewardTokens.length` and `isRewardToken[_rewardToken]` no longer reconcile, violating the invariant that a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor snapshots a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker funds the action with a flash loan of the staking token repaid in the same transaction, have the attacker run `updateFor(address _account)`, then assert the victim's claimable value and the `rewardTokens.length` versus `isRewardToken[_rewardToken]` relation are unchanged by the attacker's transaction.
