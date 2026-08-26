# Q3335: BaseRewardPool.getReward - rewardTokens array grows without bound and without removal

## Question
In rewards/BaseRewardPool.sol, queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Starting from a state where the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, can an unprivileged EOA use `getReward(address _account, address _receiver)` to leave `totalStaked()` inconsistent with `IERC20(stakingToken).balanceOf(operator)`, violating the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getReward(address _account, address _receiver)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the timing of the claim, reachable through MasterMagpie.multiclaim
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, then assert `totalStaked()` and `IERC20(stakingToken).balanceOf(operator)` end identical in both runs.
