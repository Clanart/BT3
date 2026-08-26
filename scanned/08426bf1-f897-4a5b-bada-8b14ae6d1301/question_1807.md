# Q1807: mWomSV.startUnlock - slot reuse resets the cooldown clock

## Question
In wombat/mWomSV.sol, getNextAvailableUnlockSlot() hands back the first index with amountInCoolDown == 0 once the array reaches maxSlot, and startUnlock overwrites that slot's startTime and endTime, so committed cooldown time can be recycled. Does `startUnlock(uint256 _amountToCoolDown)` let an unprivileged caller exploit that under the attacker arrived through SmartWomConvert.convertFor with _mode == 2, so that `userUnlockings[user][i].amountInCoolDown` diverges from `maxSlot`, the invariant that cooldown already served must not be transferable to a newly committed amount is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: slot reuse resets the cooldown clock)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: getNextAvailableUnlockSlot() hands back the first index with amountInCoolDown == 0 once the array reaches maxSlot, and startUnlock overwrites that slot's startTime and endTime, so committed cooldown time can be recycled. Precondition: the attacker arrived through SmartWomConvert.convertFor with _mode == 2.
- Invariant to test: cooldown already served must not be transferable to a newly committed amount; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker arrived through SmartWomConvert.convertFor with _mode == 2, call `startUnlock(uint256 _amountToCoolDown)`, and assert `userUnlockings[user][i].amountInCoolDown` equals `maxSlot` and that no account can withdraw more than it put in.
