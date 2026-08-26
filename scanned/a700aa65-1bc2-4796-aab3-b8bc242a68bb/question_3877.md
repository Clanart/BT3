# Q3877: mWomSV.startUnlock - slot reuse resets the cooldown clock

## Question
In wombat/mWomSV.sol, getNextAvailableUnlockSlot() hands back the first index with amountInCoolDown == 0 once the array reaches maxSlot, and startUnlock overwrites that slot's startTime and endTime, so committed cooldown time can be recycled. Starting from a state where the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, can an unprivileged EOA use `startUnlock(uint256 _amountToCoolDown)` to leave `userUnlockings[user][i].amountInCoolDown` inconsistent with `maxSlot`, violating the invariant that cooldown already served must not be transferable to a newly committed amount and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: slot reuse resets the cooldown clock)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: getNextAvailableUnlockSlot() hands back the first index with amountInCoolDown == 0 once the array reaches maxSlot, and startUnlock overwrites that slot's startTime and endTime, so committed cooldown time can be recycled. Precondition: the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder.
- Invariant to test: cooldown already served must not be transferable to a newly committed amount; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, have the attacker run `startUnlock(uint256 _amountToCoolDown)`, then assert the victim's claimable value and the `userUnlockings[user][i].amountInCoolDown` versus `maxSlot` relation are unchanged by the attacker's transaction.
