# Q3698: BaseRewardPool.getReward - _sendReward zeroes userRewards before the transfer settles

## Question
In rewards/BaseRewardPool.sol, _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Does `getReward(address _account, address _receiver)` let an unprivileged caller exploit that under a reward-manager queueNewRewards transaction is pending in the mempool, so that `totalStaked()` diverges from `IERC20(stakingToken).balanceOf(operator)`, the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getReward(address _account, address _receiver)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the timing of the claim, reachable through MasterMagpie.multiclaim
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that a reward-manager queueNewRewards transaction is pending in the mempool, fuzz the attacker inputs (the timing of the claim, reachable through MasterMagpie.multiclaim), and assert after every call that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered.
