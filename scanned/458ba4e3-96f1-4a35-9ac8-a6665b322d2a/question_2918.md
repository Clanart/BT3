# Q2918: BaseRewardPoolV2.getReward - rewardTokens array grows without bound and without removal

## Question
Consider rewards/BaseRewardPoolV2.sol, where queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Assuming the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, can an unprivileged attacker turn this into a divergence between `totalStaked()` and `IERC20(stakingToken).balanceOf(operator)` via `getReward(address _account, address _receiver)`, breaking the invariant that one misbehaving reward token must not be able to block settlement of the remaining reward tokens and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getReward(address _account, address _receiver)` (mechanism: rewardTokens array grows without bound and without removal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the timing of the claim, reachable through MasterMagpie.multiclaim
- Exploit idea: queueNewRewards() and _queueNewRewardsWithoutTransfer() push into rewardTokens with no way to remove an entry, and getReward()/updateFor() iterate the whole array, so a token that starts reverting on transfer makes the claim-all path revert for every staker forever. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: one misbehaving reward token must not be able to block settlement of the remaining reward tokens; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the timing of the claim, reachable through MasterMagpie.multiclaim) under the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, asserting on every row that one misbehaving reward token must not be able to block settlement of the remaining reward tokens.
