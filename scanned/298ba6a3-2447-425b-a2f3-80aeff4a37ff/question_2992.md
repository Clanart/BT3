# Q2992: BaseRewardPoolV2.updateFor - rewardTokens array grows without bound and without removal

## Question
rewards/BaseRewardPoolV2.sol: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. With the victim address and the exact block in which their reward index is snapshotted under attacker control and a reward-manager queueNewRewards transaction is pending in the mempool, can an unprivileged caller sequence `updateFor(address _account)` so that `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` no longer reconcile, violating the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under a reward-manager queueNewRewards transaction is pending in the mempool, asserting at the end that `rewards[_rewardToken].rewardPerTokenStored` still equals `userRewardPerTokenPaid[_rewardToken][account]` and the PoC's balance delta is non-positive.
