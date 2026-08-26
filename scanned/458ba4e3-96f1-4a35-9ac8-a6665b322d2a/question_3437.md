# Q3437: BaseRewardPool.updateFor - rewardTokens array grows without bound and without removal

## Question
Note that in rewards/BaseRewardPool.sol, queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Can an attacker holding only tokens bought on market reach it via `updateFor(address _account)` under a reward-manager queueNewRewards transaction is pending in the mempool and force `rewards[_rewardToken].rewardPerTokenStored` apart from `userRewardPerTokenPaid[_rewardToken][account]`, breaking the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a reward-manager queueNewRewards transaction is pending in the mempool, call `updateFor(address _account)`, and assert `rewards[_rewardToken].rewardPerTokenStored` equals `userRewardPerTokenPaid[_rewardToken][account]` and that no account can withdraw more than it put in.
