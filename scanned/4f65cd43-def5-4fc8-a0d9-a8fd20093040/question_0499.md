# Q0499: BaseRewardPoolV2.getRewards - _sendReward zeroes userRewards before the transfer settles

## Question
In rewards/BaseRewardPoolV2.sol, _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Does `getRewards(address _account, address _receiver, address[] _rewardTokens)` let an unprivileged caller exploit that under the pool has exactly one registered reward token and no queued backlog, so that `balanceOf(account)` diverges from `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool has exactly one registered reward token and no queued backlog, then assert `balanceOf(account)` and `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` end identical in both runs.
