# Q3251: BaseRewardPoolV2.getReward - rewardTokens array grows without bound and without removal

## Question
rewards/BaseRewardPoolV2.sol: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Under a reward-manager queueNewRewards transaction is pending in the mempool, is there an unprivileged sequence of `getReward(address _account, address _receiver)` that leaves `balanceOf(account)` unreconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, violates the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getReward(address _account, address _receiver)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the timing of the claim, reachable through MasterMagpie.multiclaim
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getReward(address _account, address _receiver)` sequence atomically under a reward-manager queueNewRewards transaction is pending in the mempool, asserting at the end that `balanceOf(account)` still equals `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` and the PoC's balance delta is non-positive.
