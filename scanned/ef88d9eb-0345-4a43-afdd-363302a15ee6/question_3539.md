# Q3539: BaseRewardPoolV2.getRewards - _sendReward zeroes userRewards before the transfer settles

## Question
In rewards/BaseRewardPoolV2.sol, _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Does `getRewards(address _account, address _receiver, address[] _rewardTokens)` let an unprivileged caller exploit that under the attacker funds the action with a flash loan of the staking token repaid in the same transaction, so that `totalStaked()` diverges from `IERC20(stakingToken).balanceOf(operator)`, the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker funds the action with a flash loan of the staking token repaid in the same transaction, have the attacker run `getRewards(address _account, address _receiver, address[] _rewardTokens)`, then assert the victim's claimable value and the `totalStaked()` versus `IERC20(stakingToken).balanceOf(operator)` relation are unchanged by the attacker's transaction.
