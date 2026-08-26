# Q2450: VLMGP.startUnlock - slot reuse through getNextAvailableUnlockSlot

## Question
VLMGP.sol: getNextAvailableUnlockSlot() returns the first index whose amountInCoolDown == 0 once length reaches maxSlot, and cancelUnlock/forceUnLock both zero that field, so startUnlock overwrites startTime and endTime of a reused slot and resets the penalty curve. With _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot under attacker control and the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, can an unprivileged caller sequence `startUnlock(uint256 _amountToCoolDown)` so that `getRewardablePercentWAD(user)` and `userUnlockings[user][i].amountInCoolDown` no longer reconcile, violating the invariant that a slot's penalty curve must not be resettable in a way that improves the exit terms of value already committed to cooldown and realising High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: slot reuse through getNextAvailableUnlockSlot)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: getNextAvailableUnlockSlot() returns the first index whose amountInCoolDown == 0 once length reaches maxSlot, and cancelUnlock/forceUnLock both zero that field, so startUnlock overwrites startTime and endTime of a reused slot and resets the penalty curve. Precondition: the attacker has an active vote registered in WombatBribeManager for the amount being unlocked.
- Invariant to test: a slot's penalty curve must not be resettable in a way that improves the exit terms of value already committed to cooldown; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `userUnlockings[user][i].amountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `startUnlock(uint256 _amountToCoolDown)`: constrain the setup so that the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, fuzz the attacker inputs (_amountToCoolDown and the timestamp that fixes startTime/endTime for the slot), and assert after every call that a slot's penalty curve must not be resettable in a way that improves the exit terms of value already committed to cooldown.
