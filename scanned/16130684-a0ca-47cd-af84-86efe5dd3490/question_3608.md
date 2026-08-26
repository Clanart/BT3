# Q3608: WombatStaking.harvest - harvest is permissionless and drives the whole fee split

## Question
In wombat/WombatStaking.sol, harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. Starting from a state where the pool is marked isPoolFeeFree so the fee loop is skipped entirely, can an unprivileged EOA use `harvest(address _lpToken)` to leave `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` inconsistent with `_liquidity burned from the receipt token`, violating the invariant that the timing of protocol fee conversion must not be selectable by an unrelated party and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest is permissionless and drives the whole fee split)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. Precondition: the pool is marked isPoolFeeFree so the fee loop is skipped entirely.
- Invariant to test: the timing of protocol fee conversion must not be selectable by an unrelated party; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool is marked isPoolFeeFree so the fee loop is skipped entirely, then assert `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` and `_liquidity burned from the receipt token` end identical in both runs.
