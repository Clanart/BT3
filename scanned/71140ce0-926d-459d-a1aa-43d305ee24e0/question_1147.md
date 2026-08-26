# Q1147: BaseRewardPool.donateRewards - _sendReward zeroes userRewards before the transfer settles

## Question
rewards/BaseRewardPool.sol - _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Can an unprivileged attacker controlling _amountReward down to one wei and which registered reward token is provisioned, under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, exploit this through `donateRewards(uint256 _amountReward, address _rewardToken)` to break the reconciliation between `balanceOf(account)` and `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` and the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, call `donateRewards(uint256 _amountReward, address _rewardToken)`, and assert `balanceOf(account)` equals `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` and that no account can withdraw more than it put in.
