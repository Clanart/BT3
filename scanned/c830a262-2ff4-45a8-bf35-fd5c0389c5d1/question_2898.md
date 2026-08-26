# Q2898: BaseRewardPool.getRewards - _sendReward zeroes userRewards before the transfer settles

## Question
rewards/BaseRewardPool.sol - _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Can an unprivileged attacker controlling the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor, under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, exploit this through `getRewards(address _account, address _receiver, address[] _rewardTokens)` to break the reconciliation between `rewardTokens.length` and `isRewardToken[_rewardToken]` and the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, have the attacker run `getRewards(address _account, address _receiver, address[] _rewardTokens)`, then assert the victim's claimable value and the `rewardTokens.length` versus `isRewardToken[_rewardToken]` relation are unchanged by the attacker's transaction.
