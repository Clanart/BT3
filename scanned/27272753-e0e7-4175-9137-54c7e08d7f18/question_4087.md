# Q4087: BaseRewardPool.updateFor - permissionless updateFor snapshots a victim's index

## Question
Note that in rewards/BaseRewardPool.sol, updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Can an attacker holding only tokens bought on market reach it via `updateFor(address _account)` under the victim has not been settled for several epochs and holds a large userRewards balance and force `rewards[_rewardToken].rewardPerTokenStored` apart from `userRewardPerTokenPaid[_rewardToken][account]`, breaking the invariant that a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor snapshots a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Precondition: the victim has not been settled for several epochs and holds a large userRewards balance.
- Invariant to test: a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the exact block in which their reward index is snapshotted) under the victim has not been settled for several epochs and holds a large userRewards balance, asserting on every row that a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change.
