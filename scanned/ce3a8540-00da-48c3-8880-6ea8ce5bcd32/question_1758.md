# Q1758: BaseRewardPoolV2.updateFor - rewardTokens array grows without bound and without removal

## Question
rewards/BaseRewardPoolV2.sol: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Under the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, is there an unprivileged sequence of `updateFor(address _account)` that leaves `rewards[_rewardToken].historicalRewards` unreconciled with `IERC20(_rewardToken).balanceOf(address(this))`, violates the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the exact block in which their reward index is snapshotted) under the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, asserting on every row that one misbehaving reward token must not be able to block settlement of the remaining reward tokens.
