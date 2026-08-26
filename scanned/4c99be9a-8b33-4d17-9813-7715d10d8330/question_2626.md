# Q2626: BaseRewardPoolV2.updateFor - rewardTokens array grows without bound and without removal

## Question
In rewards/BaseRewardPoolV2.sol, queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Starting from a state where the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, can an unprivileged EOA use `updateFor(address _account)` to leave `rewardTokens.length` inconsistent with `isRewardToken[_rewardToken]`, violating the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, call `updateFor(address _account)`, and assert `rewardTokens.length` equals `isRewardToken[_rewardToken]` and that no account can withdraw more than it put in.
