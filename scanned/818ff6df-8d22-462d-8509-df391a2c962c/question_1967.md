# Q1967: VLMGP.startUnlock - slot reuse through getNextAvailableUnlockSlot

## Question
Note that in VLMGP.sol, getNextAvailableUnlockSlot() returns the first index whose amountInCoolDown == 0 once length reaches maxSlot, and cancelUnlock/forceUnLock both zero that field, so startUnlock overwrites startTime and endTime of a reused slot and resets the penalty curve. Can an attacker holding only tokens bought on market reach it via `startUnlock(uint256 _amountToCoolDown)` under the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one and force `totalAmount` apart from `sum of userInfo[vlmgp][*].amount in MasterMagpie`, breaking the invariant that a slot's penalty curve must not be resettable in a way that improves the exit terms of value already committed to cooldown for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: slot reuse through getNextAvailableUnlockSlot)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: getNextAvailableUnlockSlot() returns the first index whose amountInCoolDown == 0 once length reaches maxSlot, and cancelUnlock/forceUnLock both zero that field, so startUnlock overwrites startTime and endTime of a reused slot and resets the penalty curve. Precondition: the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one.
- Invariant to test: a slot's penalty curve must not be resettable in a way that improves the exit terms of value already committed to cooldown; concretely, `totalAmount` must stay reconciled with `sum of userInfo[vlmgp][*].amount in MasterMagpie`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, call `startUnlock(uint256 _amountToCoolDown)`, and assert `totalAmount` equals `sum of userInfo[vlmgp][*].amount in MasterMagpie` and that no account can withdraw more than it put in.
