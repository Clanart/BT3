# Q3810: BaseRewardPool.updateFor - rewardTokens array grows without bound and without removal

## Question
In rewards/BaseRewardPool.sol, queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Starting from a state where the attacker funds the action with a flash loan of the staking token repaid in the same transaction, can an unprivileged EOA use `updateFor(address _account)` to leave `userRewards[_rewardToken][account]` inconsistent with `earned(account,_rewardToken)`, violating the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under the attacker funds the action with a flash loan of the staking token repaid in the same transaction, asserting at the end that `userRewards[_rewardToken][account]` still equals `earned(account,_rewardToken)` and the PoC's balance delta is non-positive.
