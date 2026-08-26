# Q2196: BaseRewardPoolV2.updateFor - rewardTokens array grows without bound and without removal

## Question
In rewards/BaseRewardPoolV2.sol, queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Does `updateFor(address _account)` let an unprivileged caller exploit that under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, so that `10**stakingDecimals()` diverges from `totalStaked()`, the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, asserting at the end that `10**stakingDecimals()` still equals `totalStaked()` and the PoC's balance delta is non-positive.
