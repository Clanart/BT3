# Q1919: BaseRewardPool.getReward - _sendReward zeroes userRewards before the transfer settles

## Question
Consider rewards/BaseRewardPool.sol, where _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Assuming V1 recomputes stakingDecimals() by an external IERC20Metadata call on every accrual and its _updateFor has no early-continue, can an unprivileged attacker turn this into a divergence between `10**stakingDecimals()` and `totalStaked()` via `getReward(address _account, address _receiver)`, breaking the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getReward(address _account, address _receiver)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the timing of the claim, reachable through MasterMagpie.multiclaim
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: V1 recomputes stakingDecimals() by an external IERC20Metadata call on every accrual and its _updateFor has no early-continue.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getReward(address _account, address _receiver)` sequence atomically under V1 recomputes stakingDecimals() by an external IERC20Metadata call on every accrual and its _updateFor has no early-continue, asserting at the end that `10**stakingDecimals()` still equals `totalStaked()` and the PoC's balance delta is non-positive.
