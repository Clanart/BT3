# Q1088: BaseRewardPoolV2.getRewards - _sendReward zeroes userRewards before the transfer settles

## Question
Consider rewards/BaseRewardPoolV2.sol, where _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Assuming rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, can an unprivileged attacker turn this into a divergence between `rewards[_rewardToken].queuedRewards` and `rewards[_rewardToken].rewardPerTokenStored` via `getRewards(address _account, address _receiver, address[] _rewardTokens)`, breaking the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, have the attacker run `getRewards(address _account, address _receiver, address[] _rewardTokens)`, then assert the victim's claimable value and the `rewards[_rewardToken].queuedRewards` versus `rewards[_rewardToken].rewardPerTokenStored` relation are unchanged by the attacker's transaction.
