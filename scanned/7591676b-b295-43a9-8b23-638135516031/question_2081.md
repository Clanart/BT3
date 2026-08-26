# Q2081: BaseRewardPoolV2.getReward - _sendReward zeroes userRewards before the transfer settles

## Question
rewards/BaseRewardPoolV2.sol - _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Can an unprivileged attacker controlling the timing of the claim, reachable through MasterMagpie.multiclaim, under the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, exploit this through `getReward(address _account, address _receiver)` to break the reconciliation between `rewardTokens.length` and `isRewardToken[_rewardToken]` and the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getReward(address _account, address _receiver)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the timing of the claim, reachable through MasterMagpie.multiclaim
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the timing of the claim, reachable through MasterMagpie.multiclaim) under the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, asserting on every row that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered.
