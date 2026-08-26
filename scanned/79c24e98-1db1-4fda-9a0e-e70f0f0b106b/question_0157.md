# Q0157: BaseRewardPool.updateFor - rewardTokens array grows without bound and without removal

## Question
Note that in rewards/BaseRewardPool.sol, queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Can an attacker holding only tokens bought on market reach it via `updateFor(address _account)` under the pool has exactly one registered reward token and no queued backlog and force `totalStaked()` apart from `IERC20(stakingToken).balanceOf(operator)`, breaking the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the pool has exactly one registered reward token and no queued backlog, have the attacker run `updateFor(address _account)`, then assert the victim's claimable value and the `totalStaked()` versus `IERC20(stakingToken).balanceOf(operator)` relation are unchanged by the attacker's transaction.
