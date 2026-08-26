# Q3586: BaseRewardPool.donateRewards - _sendReward zeroes userRewards before the transfer settles

## Question
In rewards/BaseRewardPool.sol, _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Starting from a state where a reward-manager queueNewRewards transaction is pending in the mempool, can an unprivileged EOA use `donateRewards(uint256 _amountReward, address _rewardToken)` to leave `rewards[_rewardToken].rewardPerTokenStored` inconsistent with `userRewardPerTokenPaid[_rewardToken][account]`, violating the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken)`: constrain the setup so that a reward-manager queueNewRewards transaction is pending in the mempool, fuzz the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned), and assert after every call that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered.
