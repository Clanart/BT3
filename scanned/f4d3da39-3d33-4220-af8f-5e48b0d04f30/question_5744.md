# Q5744: MasterMagpie.updatePool - lpSupply deflation front-run before updatePool

## Question
Note that in rewards/MasterMagpie.sol, an attacker withdraws to drive _calLpSupply() to a minimum in the same block as updatePool(), so the whole multiplier * mgpPerSec * allocPoint slice is divided by a tiny lpSupply and accMGPPerShare jumps for whoever re-enters immediately after. Can an attacker holding only tokens bought on market reach it via `updatePool(address _stakingToken)` under the contract is paused so only emergencyWithdraw is reachable and force `IBaseRewardPool(rewarder).balanceOf(user)` apart from `IBaseRewardPool(rewarder).totalStaked()`, breaking the invariant that accMGPPerShare growth per second must be proportional to time-weighted stake, not to instantaneous supply chosen by one actor for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: lpSupply deflation front-run before updatePool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: an attacker withdraws to drive _calLpSupply() to a minimum in the same block as updatePool(), so the whole multiplier * mgpPerSec * allocPoint slice is divided by a tiny lpSupply and accMGPPerShare jumps for whoever re-enters immediately after. Precondition: the contract is paused so only emergencyWithdraw is reachable.
- Invariant to test: accMGPPerShare growth per second must be proportional to time-weighted stake, not to instantaneous supply chosen by one actor; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_stakingToken and the timestamp at which accMGPPerShare is rolled forward) under the contract is paused so only emergencyWithdraw is reachable, asserting on every row that accMGPPerShare growth per second must be proportional to time-weighted stake, not to instantaneous supply chosen by one actor.
