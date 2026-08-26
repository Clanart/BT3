# Q5417: MasterMagpie.updatePool - lpSupply deflation front-run before updatePool

## Question
rewards/MasterMagpie.sol: an attacker withdraws to drive _calLpSupply() to a minimum in the same block as updatePool(), so the whole multiplier * mgpPerSec * allocPoint slice is divided by a tiny lpSupply and accMGPPerShare jumps for whoever re-enters immediately after. Under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), is there an unprivileged sequence of `updatePool(address _stakingToken)` that leaves `_calLpSupply(_stakingToken)` unreconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`, violates the invariant that accMGPPerShare growth per second must be proportional to time-weighted stake, not to instantaneous supply chosen by one actor, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: lpSupply deflation front-run before updatePool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: an attacker withdraws to drive _calLpSupply() to a minimum in the same block as updatePool(), so the whole multiplier * mgpPerSec * allocPoint slice is divided by a tiny lpSupply and accMGPPerShare jumps for whoever re-enters immediately after. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked().
- Invariant to test: accMGPPerShare growth per second must be proportional to time-weighted stake, not to instantaneous supply chosen by one actor; concretely, `_calLpSupply(_stakingToken)` must stay reconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_stakingToken and the timestamp at which accMGPPerShare is rolled forward) under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), asserting on every row that accMGPPerShare growth per second must be proportional to time-weighted stake, not to instantaneous supply chosen by one actor.
