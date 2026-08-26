# Q4997: BaseRewardPool.updateFor - _sendReward zeroes userRewards before the transfer settles

## Question
In rewards/BaseRewardPool.sol, _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Does `updateFor(address _account)` let an unprivileged caller exploit that under the attacker calls the function twice in the same block to observe the second, early-continued iteration, so that `rewards[_rewardToken].queuedRewards` diverges from `rewards[_rewardToken].rewardPerTokenStored`, the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: the attacker calls the function twice in the same block to observe the second, early-continued iteration.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under the attacker calls the function twice in the same block to observe the second, early-continued iteration, asserting at the end that `rewards[_rewardToken].queuedRewards` still equals `rewards[_rewardToken].rewardPerTokenStored` and the PoC's balance delta is non-positive.
