# Q3635: BaseRewardPoolV2.updateFor - rewardTokens array grows without bound and without removal

## Question
rewards/BaseRewardPoolV2.sol - queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Can an unprivileged attacker controlling the victim address and the exact block in which their reward index is snapshotted, under the victim has not been settled for several epochs and holds a large userRewards balance, exploit this through `updateFor(address _account)` to break the reconciliation between `totalStaked()` and `IERC20(stakingToken).balanceOf(operator)` and the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: the victim has not been settled for several epochs and holds a large userRewards balance.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the victim has not been settled for several epochs and holds a large userRewards balance, have the attacker run `updateFor(address _account)`, then assert the victim's claimable value and the `totalStaked()` versus `IERC20(stakingToken).balanceOf(operator)` relation are unchanged by the attacker's transaction.
