# Q0002: BaseRewardPool.updateFor - permissionless updateFor snapshots a victim's index

## Question
rewards/BaseRewardPool.sol: updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. With the victim address and the exact block in which their reward index is snapshotted under attacker control and the pool has exactly one registered reward token and no queued backlog, can an unprivileged caller sequence `updateFor(address _account)` so that `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` no longer reconcile, violating the invariant that a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor snapshots a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool has exactly one registered reward token and no queued backlog, call `updateFor(address _account)`, and assert `rewards[_rewardToken].rewardPerTokenStored` equals `userRewardPerTokenPaid[_rewardToken][account]` and that no account can withdraw more than it put in.
