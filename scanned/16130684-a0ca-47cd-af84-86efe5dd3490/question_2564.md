# Q2564: BaseRewardPoolV2.updateFor - permissionless updateFor snapshots a victim's index

## Question
In rewards/BaseRewardPoolV2.sol, updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Starting from a state where the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, can an unprivileged EOA use `updateFor(address _account)` to leave `rewards[_rewardToken].historicalRewards` inconsistent with `IERC20(_rewardToken).balanceOf(address(this))`, violating the invariant that a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor snapshots a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the exact block in which their reward index is snapshotted) under the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, asserting on every row that a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change.
