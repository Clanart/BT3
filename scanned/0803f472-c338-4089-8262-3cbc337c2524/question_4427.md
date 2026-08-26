# Q4427: BaseRewardPoolV2.updateFor - permissionless updateFor snapshots a victim's index

## Question
Consider rewards/BaseRewardPoolV2.sol, where updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Assuming the attacker calls the function twice in the same block to observe the second, early-continued iteration, can an unprivileged attacker turn this into a divergence between `balanceOf(account)` and `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` via `updateFor(address _account)`, breaking the invariant that a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor snapshots a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Precondition: the attacker calls the function twice in the same block to observe the second, early-continued iteration.
- Invariant to test: a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the exact block in which their reward index is snapshotted) under the attacker calls the function twice in the same block to observe the second, early-continued iteration, asserting on every row that a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change.
