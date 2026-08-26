# Q4178: BaseRewardPoolV2.updateFor - permissionless updateFor snapshots a victim's index

## Question
rewards/BaseRewardPoolV2.sol: updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. With the victim address and the exact block in which their reward index is snapshotted under attacker control and a previously registered reward token has begun reverting on transfer, can an unprivileged caller sequence `updateFor(address _account)` so that `totalStaked()` and `IERC20(stakingToken).balanceOf(operator)` no longer reconcile, violating the invariant that a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor snapshots a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Precondition: a previously registered reward token has begun reverting on transfer.
- Invariant to test: a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a previously registered reward token has begun reverting on transfer, call `updateFor(address _account)`, and assert `totalStaked()` equals `IERC20(stakingToken).balanceOf(operator)` and that no account can withdraw more than it put in.
