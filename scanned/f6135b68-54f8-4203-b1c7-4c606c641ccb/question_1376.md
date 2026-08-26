# Q1376: mWomSV.startUnlock - matured slot decays the rewardable percent toward zero

## Question
In wombat/mWomSV.sol, for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Can an unprivileged attacker reach this through `startUnlock(uint256 _amountToCoolDown)` while the attacker reached maxSlot so slot reuse is forced, and drive `mWomSV.getUserTotalLocked(user)` out of agreement with `ArbWomUp3.calDoubledCounted(user)` - breaking the invariant that a user must not lose vested value merely because they redeemed late - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker reached maxSlot so slot reuse is forced.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `mWomSV.getUserTotalLocked(user)` must stay reconciled with `ArbWomUp3.calDoubledCounted(user)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker reached maxSlot so slot reuse is forced, have the attacker run `startUnlock(uint256 _amountToCoolDown)`, then assert the victim's claimable value and the `mWomSV.getUserTotalLocked(user)` versus `ArbWomUp3.calDoubledCounted(user)` relation are unchanged by the attacker's transaction.
