# Q4931: BaseRewardPool.getReward - _sendReward zeroes userRewards before the transfer settles

## Question
Note that in rewards/BaseRewardPool.sol, _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Can an attacker holding only tokens bought on market reach it via `getReward(address _account, address _receiver)` under a previously registered reward token has begun reverting on transfer and force `10**stakingDecimals()` apart from `totalStaked()`, breaking the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getReward(address _account, address _receiver)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the timing of the claim, reachable through MasterMagpie.multiclaim
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: a previously registered reward token has begun reverting on transfer.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up a previously registered reward token has begun reverting on transfer, snapshot `10**stakingDecimals()` and `totalStaked()`, run the attacker's `getReward(address _account, address _receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
