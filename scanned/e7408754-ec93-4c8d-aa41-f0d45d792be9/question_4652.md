# Q4652: BaseRewardPool.getRewards - _sendReward zeroes userRewards before the transfer settles

## Question
Note that in rewards/BaseRewardPool.sol, _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Can an attacker holding only tokens bought on market reach it via `getRewards(address _account, address _receiver, address[] _rewardTokens)` under the reward token charges a transfer fee so the received balance is below the requested amount and force `rewards[_rewardToken].queuedRewards` apart from `rewards[_rewardToken].rewardPerTokenStored`, breaking the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: the reward token charges a transfer fee so the received balance is below the requested amount.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the reward token charges a transfer fee so the received balance is below the requested amount, snapshot `rewards[_rewardToken].queuedRewards` and `rewards[_rewardToken].rewardPerTokenStored`, run the attacker's `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
