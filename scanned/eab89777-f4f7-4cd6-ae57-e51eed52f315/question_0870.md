# Q0870: BaseRewardPool.updateFor - rewardTokens array grows without bound and without removal

## Question
rewards/BaseRewardPool.sol: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, is there an unprivileged sequence of `updateFor(address _account)` that leaves `balanceOf(account)` unreconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, violates the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, have the attacker run `updateFor(address _account)`, then assert the victim's claimable value and the `balanceOf(account)` versus `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` relation are unchanged by the attacker's transaction.
