# Q2936: BaseRewardPool.getReward - rewardTokens array grows without bound and without removal

## Question
rewards/BaseRewardPool.sol - queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Can an unprivileged attacker controlling the timing of the claim, reachable through MasterMagpie.multiclaim, under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, exploit this through `getReward(address _account, address _receiver)` to break the reconciliation between `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` and the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getReward(address _account, address _receiver)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the timing of the claim, reachable through MasterMagpie.multiclaim
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, fuzz the attacker inputs (the timing of the claim, reachable through MasterMagpie.multiclaim), and assert after every call that one misbehaving reward token must not be able to block settlement of the remaining reward tokens.
