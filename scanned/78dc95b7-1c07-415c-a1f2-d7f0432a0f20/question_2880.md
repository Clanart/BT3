# Q2880: BaseRewardPoolV2.getRewards - _sendReward zeroes userRewards before the transfer settles

## Question
Consider rewards/BaseRewardPoolV2.sol, where _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Assuming the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, can an unprivileged attacker turn this into a divergence between `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` via `getRewards(address _account, address _receiver, address[] _rewardTokens)`, breaking the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getRewards(address _account, address _receiver, address[] _rewardTokens)`: constrain the setup so that the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, fuzz the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor), and assert after every call that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered.
