# Q2448: BaseRewardPool.getReward - _sendReward zeroes userRewards before the transfer settles

## Question
In rewards/BaseRewardPool.sol, _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Starting from a state where the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, can an unprivileged EOA use `getReward(address _account, address _receiver)` to leave `rewardTokens.length` inconsistent with `isRewardToken[_rewardToken]`, violating the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getReward(address _account, address _receiver)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the timing of the claim, reachable through MasterMagpie.multiclaim
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, fuzz the attacker inputs (the timing of the claim, reachable through MasterMagpie.multiclaim), and assert after every call that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered.
