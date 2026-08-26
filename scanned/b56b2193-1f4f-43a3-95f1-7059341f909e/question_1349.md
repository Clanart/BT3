# Q1349: mWomSV.startUnlock - slot reuse resets the cooldown clock

## Question
In wombat/mWomSV.sol, getNextAvailableUnlockSlot() hands back the first index with amountInCoolDown == 0 once the array reaches maxSlot, and startUnlock overwrites that slot's startTime and endTime, so committed cooldown time can be recycled. Starting from a state where the attacker reached maxSlot so slot reuse is forced, can an unprivileged EOA use `startUnlock(uint256 _amountToCoolDown)` to leave `getRewardablePercentWAD(user)` inconsistent with `_calExpireForfeit in mWOMSVBaseRewarder`, violating the invariant that cooldown already served must not be transferable to a newly committed amount and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: slot reuse resets the cooldown clock)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: getNextAvailableUnlockSlot() hands back the first index with amountInCoolDown == 0 once the array reaches maxSlot, and startUnlock overwrites that slot's startTime and endTime, so committed cooldown time can be recycled. Precondition: the attacker reached maxSlot so slot reuse is forced.
- Invariant to test: cooldown already served must not be transferable to a newly committed amount; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `startUnlock(uint256 _amountToCoolDown)` sequence atomically under the attacker reached maxSlot so slot reuse is forced, asserting at the end that `getRewardablePercentWAD(user)` still equals `_calExpireForfeit in mWOMSVBaseRewarder` and the PoC's balance delta is non-positive.
