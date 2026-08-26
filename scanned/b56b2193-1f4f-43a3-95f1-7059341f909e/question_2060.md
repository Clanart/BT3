# Q2060: mWomSV.lock - matured slot decays the rewardable percent toward zero

## Question
Consider wombat/mWomSV.sol, where for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Assuming the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, can an unprivileged attacker turn this into a divergence between `mWomSV.getUserTotalLocked(user)` and `ArbWomUp3.calDoubledCounted(user)` via `lock(uint256 _amount)`, breaking the invariant that a user must not lose vested value merely because they redeemed late and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lock(uint256 _amount)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the mWOM lock is credited
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `mWomSV.getUserTotalLocked(user)` must stay reconciled with `ArbWomUp3.calDoubledCounted(user)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, have the attacker run `lock(uint256 _amount)`, then assert the victim's claimable value and the `mWomSV.getUserTotalLocked(user)` versus `ArbWomUp3.calDoubledCounted(user)` relation are unchanged by the attacker's transaction.
