# Q5137: MasterMagpie.updatePool - lpSupply deflation front-run before updatePool

## Question
rewards/MasterMagpie.sol: an attacker withdraws to drive _calLpSupply() to a minimum in the same block as updatePool(), so the whole multiplier * mgpPerSec * allocPoint slice is divided by a tiny lpSupply and accMGPPerShare jumps for whoever re-enters immediately after. Under the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, is there an unprivileged sequence of `updatePool(address _stakingToken)` that leaves `unClaimedMgp[_stakingToken][user]` unreconciled with `userInfo[_stakingToken][user].rewardDebt`, violates the invariant that accMGPPerShare growth per second must be proportional to time-weighted stake, not to instantaneous supply chosen by one actor, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: lpSupply deflation front-run before updatePool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: an attacker withdraws to drive _calLpSupply() to a minimum in the same block as updatePool(), so the whole multiplier * mgpPerSec * allocPoint slice is divided by a tiny lpSupply and accMGPPerShare jumps for whoever re-enters immediately after. Precondition: the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals.
- Invariant to test: accMGPPerShare growth per second must be proportional to time-weighted stake, not to instantaneous supply chosen by one actor; concretely, `unClaimedMgp[_stakingToken][user]` must stay reconciled with `userInfo[_stakingToken][user].rewardDebt`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, have the attacker run `updatePool(address _stakingToken)`, then assert the victim's claimable value and the `unClaimedMgp[_stakingToken][user]` versus `userInfo[_stakingToken][user].rewardDebt` relation are unchanged by the attacker's transaction.
