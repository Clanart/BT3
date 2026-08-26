# Q3714: BaseRewardPool.getReward - rewardTokens array grows without bound and without removal

## Question
Note that in rewards/BaseRewardPool.sol, queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Can an attacker holding only tokens bought on market reach it via `getReward(address _account, address _receiver)` under a reward-manager queueNewRewards transaction is pending in the mempool and force `balanceOf(account)` apart from `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, breaking the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getReward(address _account, address _receiver)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the timing of the claim, reachable through MasterMagpie.multiclaim
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a reward-manager queueNewRewards transaction is pending in the mempool, have the attacker run `getReward(address _account, address _receiver)`, then assert the victim's claimable value and the `balanceOf(account)` versus `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` relation are unchanged by the attacker's transaction.
