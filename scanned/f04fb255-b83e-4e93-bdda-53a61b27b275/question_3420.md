# Q3420: BaseRewardPool.updateFor - _sendReward zeroes userRewards before the transfer settles

## Question
rewards/BaseRewardPool.sol - _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Can an unprivileged attacker controlling the victim address and the exact block in which their reward index is snapshotted, under a reward-manager queueNewRewards transaction is pending in the mempool, exploit this through `updateFor(address _account)` to break the reconciliation between `rewardTokens.length` and `isRewardToken[_rewardToken]` and the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under a reward-manager queueNewRewards transaction is pending in the mempool, asserting at the end that `rewardTokens.length` still equals `isRewardToken[_rewardToken]` and the PoC's balance delta is non-positive.
