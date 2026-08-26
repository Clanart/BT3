# Q3319: BaseRewardPoolV2.updateFor - rewardTokens array grows without bound and without removal

## Question
In rewards/BaseRewardPoolV2.sol, queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Can an unprivileged attacker reach this through `updateFor(address _account)` while the attacker funds the action with a flash loan of the staking token repaid in the same transaction, and drive `userRewards[_rewardToken][account]` out of agreement with `earned(account,_rewardToken)` - breaking the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker funds the action with a flash loan of the staking token repaid in the same transaction, then assert `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` end identical in both runs.
