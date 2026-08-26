# Q0530: BaseRewardPoolV2.getReward - _sendReward zeroes userRewards before the transfer settles

## Question
In rewards/BaseRewardPoolV2.sol, _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Does `getReward(address _account, address _receiver)` let an unprivileged caller exploit that under the pool has exactly one registered reward token and no queued backlog, so that `rewards[_rewardToken].queuedRewards` diverges from `rewards[_rewardToken].rewardPerTokenStored`, the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getReward(address _account, address _receiver)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the timing of the claim, reachable through MasterMagpie.multiclaim
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the timing of the claim, reachable through MasterMagpie.multiclaim) under the pool has exactly one registered reward token and no queued backlog, asserting on every row that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered.
