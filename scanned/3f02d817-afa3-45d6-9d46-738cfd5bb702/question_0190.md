# Q0190: VLMGP.startUnlock - slot reuse through getNextAvailableUnlockSlot

## Question
In VLMGP.sol, getNextAvailableUnlockSlot() returns the first index whose amountInCoolDown == 0 once length reaches maxSlot, and cancelUnlock/forceUnLock both zero that field, so startUnlock overwrites startTime and endTime of a reused slot and resets the penalty curve. Does `startUnlock(uint256 _amountToCoolDown)` let an unprivileged caller exploit that under the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, so that `maxSlot` diverges from `userUnlockings[user].length`, the invariant that a slot's penalty curve must not be resettable in a way that improves the exit terms of value already committed to cooldown is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: slot reuse through getNextAvailableUnlockSlot)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: getNextAvailableUnlockSlot() returns the first index whose amountInCoolDown == 0 once length reaches maxSlot, and cancelUnlock/forceUnLock both zero that field, so startUnlock overwrites startTime and endTime of a reused slot and resets the penalty curve. Precondition: the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18.
- Invariant to test: a slot's penalty curve must not be resettable in a way that improves the exit terms of value already committed to cooldown; concretely, `maxSlot` must stay reconciled with `userUnlockings[user].length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountToCoolDown and the timestamp that fixes startTime/endTime for the slot) under the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, asserting on every row that a slot's penalty curve must not be resettable in a way that improves the exit terms of value already committed to cooldown.
