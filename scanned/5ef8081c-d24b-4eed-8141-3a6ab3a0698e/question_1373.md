# Q1373: BaseRewardPool.getReward - rewardTokens array grows without bound and without removal

## Question
rewards/BaseRewardPool.sol: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. With the timing of the claim, reachable through MasterMagpie.multiclaim under attacker control and rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, can an unprivileged caller sequence `getReward(address _account, address _receiver)` so that `10**stakingDecimals()` and `totalStaked()` no longer reconcile, violating the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getReward(address _account, address _receiver)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the timing of the claim, reachable through MasterMagpie.multiclaim
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getReward(address _account, address _receiver)` sequence atomically under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, asserting at the end that `10**stakingDecimals()` still equals `totalStaked()` and the PoC's balance delta is non-positive.
