# Q3605: mWomSV.startUnlock - matured slot decays the rewardable percent toward zero

## Question
In wombat/mWomSV.sol, for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Does `startUnlock(uint256 _amountToCoolDown)` let an unprivileged caller exploit that under the attacker repeats cancelUnlock and startUnlock inside one transaction, so that `mWomSV.getUserTotalLocked(user)` diverges from `ArbWomUp3.calDoubledCounted(user)`, the invariant that a user must not lose vested value merely because they redeemed late is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker repeats cancelUnlock and startUnlock inside one transaction.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `mWomSV.getUserTotalLocked(user)` must stay reconciled with `ArbWomUp3.calDoubledCounted(user)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker repeats cancelUnlock and startUnlock inside one transaction, snapshot `mWomSV.getUserTotalLocked(user)` and `ArbWomUp3.calDoubledCounted(user)`, run the attacker's `startUnlock(uint256 _amountToCoolDown)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
