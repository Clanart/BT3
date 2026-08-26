# Q4748: BaseRewardPool.updateFor - rewardTokens array grows without bound and without removal

## Question
In rewards/BaseRewardPool.sol, queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Does `updateFor(address _account)` let an unprivileged caller exploit that under a previously registered reward token has begun reverting on transfer, so that `rewards[_rewardToken].queuedRewards` diverges from `rewards[_rewardToken].rewardPerTokenStored`, the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: a previously registered reward token has begun reverting on transfer.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up a previously registered reward token has begun reverting on transfer, snapshot `rewards[_rewardToken].queuedRewards` and `rewards[_rewardToken].rewardPerTokenStored`, run the attacker's `updateFor(address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
