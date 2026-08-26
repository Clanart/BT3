# Q0842: mWomSV.startUnlock - matured slot decays the rewardable percent toward zero

## Question
Consider wombat/mWomSV.sol, where for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Assuming the attacker's slot matured one block ago, can an unprivileged attacker turn this into a divergence between `userUnlockings[user][i].amountInCoolDown` and `maxSlot` via `startUnlock(uint256 _amountToCoolDown)`, breaking the invariant that a user must not lose vested value merely because they redeemed late and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker's slot matured one block ago.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `startUnlock(uint256 _amountToCoolDown)`: constrain the setup so that the attacker's slot matured one block ago, fuzz the attacker inputs (_amountToCoolDown and the timestamps written into the slot), and assert after every call that a user must not lose vested value merely because they redeemed late.
