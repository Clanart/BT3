# Q4953: BaseRewardPool.updateFor - permissionless updateFor snapshots a victim's index

## Question
In rewards/BaseRewardPool.sol, updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Can an unprivileged attacker reach this through `updateFor(address _account)` while the attacker calls the function twice in the same block to observe the second, early-continued iteration, and drive `balanceOf(account)` out of agreement with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` - breaking the invariant that a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor snapshots a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: updateFor(address) carries no access control and writes userRewards[token][_account] = earned(...) and userRewardPerTokenPaid[token][_account] = rewardPerToken(token) for any address, so an attacker fixes a victim's accrual at a moment of the attacker's choosing. Precondition: the attacker calls the function twice in the same block to observe the second, early-continued iteration.
- Invariant to test: a user's reward index may only be advanced by that user's own action or by an operator call tied to a real balance change; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker calls the function twice in the same block to observe the second, early-continued iteration, snapshot `balanceOf(account)` and `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, run the attacker's `updateFor(address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
