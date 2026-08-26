# Q3952: BaseRewardPool.donateRewards - _sendReward zeroes userRewards before the transfer settles

## Question
rewards/BaseRewardPool.sol: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. With _amountReward down to one wei and which registered reward token is provisioned under attacker control and the attacker funds the action with a flash loan of the staking token repaid in the same transaction, can an unprivileged caller sequence `donateRewards(uint256 _amountReward, address _rewardToken)` so that `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` no longer reconcile, violating the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker funds the action with a flash loan of the staking token repaid in the same transaction, have the attacker run `donateRewards(uint256 _amountReward, address _rewardToken)`, then assert the victim's claimable value and the `userRewards[_rewardToken][account]` versus `earned(account,_rewardToken)` relation are unchanged by the attacker's transaction.
