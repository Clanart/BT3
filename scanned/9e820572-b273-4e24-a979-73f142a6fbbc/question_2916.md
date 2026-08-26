# Q2916: MasterMagpie.updatePool - lpSupply deflation front-run before updatePool

## Question
Consider rewards/MasterMagpie.sol, where an attacker withdraws to drive _calLpSupply() to a minimum in the same block as updatePool(), so the whole multiplier * mgpPerSec * allocPoint slice is divided by a tiny lpSupply and accMGPPerShare jumps for whoever re-enters immediately after. Assuming the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, can an unprivileged attacker turn this into a divergence between `mgpPerSec` and `IERC20(mgp).balanceOf(masterMagpie)` via `updatePool(address _stakingToken)`, breaking the invariant that accMGPPerShare growth per second must be proportional to time-weighted stake, not to instantaneous supply chosen by one actor and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: lpSupply deflation front-run before updatePool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: an attacker withdraws to drive _calLpSupply() to a minimum in the same block as updatePool(), so the whole multiplier * mgpPerSec * allocPoint slice is divided by a tiny lpSupply and accMGPPerShare jumps for whoever re-enters immediately after. Precondition: the attacker holds one wei of stake so lpSupply is non-zero but every division truncates.
- Invariant to test: accMGPPerShare growth per second must be proportional to time-weighted stake, not to instantaneous supply chosen by one actor; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_stakingToken and the timestamp at which accMGPPerShare is rolled forward) under the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, asserting on every row that accMGPPerShare growth per second must be proportional to time-weighted stake, not to instantaneous supply chosen by one actor.
