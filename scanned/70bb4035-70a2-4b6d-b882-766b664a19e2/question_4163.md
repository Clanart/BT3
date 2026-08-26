# Q4163: BaseRewardPoolV2.getReward - rewardTokens array grows without bound and without removal

## Question
rewards/BaseRewardPoolV2.sol: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Under the reward token charges a transfer fee so the received balance is below the requested amount, is there an unprivileged sequence of `getReward(address _account, address _receiver)` that leaves `10**stakingDecimals()` unreconciled with `totalStaked()`, violates the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getReward(address _account, address _receiver)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the timing of the claim, reachable through MasterMagpie.multiclaim
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: the reward token charges a transfer fee so the received balance is below the requested amount.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that the reward token charges a transfer fee so the received balance is below the requested amount, fuzz the attacker inputs (the timing of the claim, reachable through MasterMagpie.multiclaim), and assert after every call that one misbehaving reward token must not be able to block settlement of the remaining reward tokens.
