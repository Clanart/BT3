# Q0715: BaseRewardPool.updateFor - permissionless updateFor snapshots a victim's index

## Question
Consider rewards/BaseRewardPool.sol, where updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Assuming rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, can an unprivileged attacker turn this into a divergence between `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` via `updateFor(address _account)`, breaking the invariant that a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor snapshots a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, call `updateFor(address _account)`, and assert `userRewards[_rewardToken][account]` equals `earned(account,_rewardToken)` and that no account can withdraw more than it put in.
