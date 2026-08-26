# Q2425: BaseRewardPool.getRewards - _sendReward zeroes userRewards before the transfer settles

## Question
In rewards/BaseRewardPool.sol, _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Starting from a state where the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, can an unprivileged EOA use `getRewards(address _account, address _receiver, address[] _rewardTokens)` to leave `10**stakingDecimals()` inconsistent with `totalStaked()`, violating the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, snapshot `10**stakingDecimals()` and `totalStaked()`, run the attacker's `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
