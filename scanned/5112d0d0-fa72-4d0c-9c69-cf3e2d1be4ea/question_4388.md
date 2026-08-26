# Q4388: BaseRewardPoolV2.getRewards - _sendReward zeroes userRewards before the transfer settles

## Question
Note that in rewards/BaseRewardPoolV2.sol, _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Can an attacker holding only tokens bought on market reach it via `getRewards(address _account, address _receiver, address[] _rewardTokens)` under a previously registered reward token has begun reverting on transfer and force `rewards[_rewardToken].historicalRewards` apart from `IERC20(_rewardToken).balanceOf(address(this))`, breaking the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: a previously registered reward token has begun reverting on transfer.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a previously registered reward token has begun reverting on transfer, then assert `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` end identical in both runs.
