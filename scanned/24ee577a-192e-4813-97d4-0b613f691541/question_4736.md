# Q4736: BaseRewardPool.updateFor - _sendReward zeroes userRewards before the transfer settles

## Question
In rewards/BaseRewardPool.sol, _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Can an unprivileged attacker reach this through `updateFor(address _account)` while a previously registered reward token has begun reverting on transfer, and drive `balanceOf(account)` out of agreement with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` - breaking the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: a previously registered reward token has begun reverting on transfer.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a previously registered reward token has begun reverting on transfer, call `updateFor(address _account)`, and assert `balanceOf(account)` equals `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` and that no account can withdraw more than it put in.
