# Q2605: BaseRewardPool.updateFor - rewardTokens array grows without bound and without removal

## Question
Note that in rewards/BaseRewardPool.sol, queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Can an attacker holding only tokens bought on market reach it via `updateFor(address _account)` under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small and force `10**stakingDecimals()` apart from `totalStaked()`, breaking the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, snapshot `10**stakingDecimals()` and `totalStaked()`, run the attacker's `updateFor(address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
