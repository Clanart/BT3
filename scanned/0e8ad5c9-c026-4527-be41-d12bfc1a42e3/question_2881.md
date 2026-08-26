# Q2881: VLMGP.startUnlock - slot reuse through getNextAvailableUnlockSlot

## Question
In VLMGP.sol, getNextAvailableUnlockSlot() returns the first index whose amountInCoolDown == 0 once length reaches maxSlot, and cancelUnlock/forceUnLock both zero that field, so startUnlock overwrites startTime and endTime of a reused slot and resets the penalty curve. Can an unprivileged attacker reach this through `startUnlock(uint256 _amountToCoolDown)` while the pool the attacker voted for has since been deactivated so unvote reverts, and drive `userUnlockings[user][i].endTime` out of agreement with `block.timestamp` - breaking the invariant that a slot's penalty curve must not be resettable in a way that improves the exit terms of value already committed to cooldown - for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: slot reuse through getNextAvailableUnlockSlot)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: getNextAvailableUnlockSlot() returns the first index whose amountInCoolDown == 0 once length reaches maxSlot, and cancelUnlock/forceUnLock both zero that field, so startUnlock overwrites startTime and endTime of a reused slot and resets the penalty curve. Precondition: the pool the attacker voted for has since been deactivated so unvote reverts.
- Invariant to test: a slot's penalty curve must not be resettable in a way that improves the exit terms of value already committed to cooldown; concretely, `userUnlockings[user][i].endTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the pool the attacker voted for has since been deactivated so unvote reverts, snapshot `userUnlockings[user][i].endTime` and `block.timestamp`, run the attacker's `startUnlock(uint256 _amountToCoolDown)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
