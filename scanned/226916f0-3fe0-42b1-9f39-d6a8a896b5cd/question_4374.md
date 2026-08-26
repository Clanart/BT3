# Q4374: BaseRewardPool.getReward - _sendReward zeroes userRewards before the transfer settles

## Question
rewards/BaseRewardPool.sol: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. With the timing of the claim, reachable through MasterMagpie.multiclaim under attacker control and the victim has not been settled for several epochs and holds a large userRewards balance, can an unprivileged caller sequence `getReward(address _account, address _receiver)` so that `rewards[_rewardToken].queuedRewards` and `rewards[_rewardToken].rewardPerTokenStored` no longer reconcile, violating the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getReward(address _account, address _receiver)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the timing of the claim, reachable through MasterMagpie.multiclaim
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: the victim has not been settled for several epochs and holds a large userRewards balance.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the victim has not been settled for several epochs and holds a large userRewards balance, call `getReward(address _account, address _receiver)`, and assert `rewards[_rewardToken].queuedRewards` equals `rewards[_rewardToken].rewardPerTokenStored` and that no account can withdraw more than it put in.
