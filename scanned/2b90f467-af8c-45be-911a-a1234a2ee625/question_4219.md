# Q4219: BaseRewardPoolV2.updateFor - rewardTokens array grows without bound and without removal

## Question
rewards/BaseRewardPoolV2.sol - queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Can an unprivileged attacker controlling the victim address and the exact block in which their reward index is snapshotted, under a previously registered reward token has begun reverting on transfer, exploit this through `updateFor(address _account)` to break the reconciliation between `rewards[_rewardToken].queuedRewards` and `rewards[_rewardToken].rewardPerTokenStored` and the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: a previously registered reward token has begun reverting on transfer.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a previously registered reward token has begun reverting on transfer, then assert `rewards[_rewardToken].queuedRewards` and `rewards[_rewardToken].rewardPerTokenStored` end identical in both runs.
