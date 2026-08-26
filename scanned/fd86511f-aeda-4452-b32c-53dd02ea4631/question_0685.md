# Q0685: BaseRewardPoolV2.updateFor - rewardTokens array grows without bound and without removal

## Question
Note that in rewards/BaseRewardPoolV2.sol, queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Can an attacker holding only tokens bought on market reach it via `updateFor(address _account)` under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero and force `balanceOf(account)` apart from `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, breaking the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, fuzz the attacker inputs (the victim address and the exact block in which their reward index is snapshotted), and assert after every call that one misbehaving reward token must not be able to block settlement of the remaining reward tokens.
