# Q1118: BaseRewardPoolV2.getReward - _sendReward zeroes userRewards before the transfer settles

## Question
rewards/BaseRewardPoolV2.sol: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. With the timing of the claim, reachable through MasterMagpie.multiclaim under attacker control and rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, can an unprivileged caller sequence `getReward(address _account, address _receiver)` so that `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` no longer reconcile, violating the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getReward(address _account, address _receiver)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the timing of the claim, reachable through MasterMagpie.multiclaim
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, snapshot `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))`, run the attacker's `getReward(address _account, address _receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
