# Q3604: VLMGP.startUnlock - slot reuse through getNextAvailableUnlockSlot

## Question
In VLMGP.sol, getNextAvailableUnlockSlot() returns the first index whose amountInCoolDown == 0 once length reaches maxSlot, and cancelUnlock/forceUnLock both zero that field, so startUnlock overwrites startTime and endTime of a reused slot and resets the penalty curve. Starting from a state where the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, can an unprivileged EOA use `startUnlock(uint256 _amountToCoolDown)` to leave `userInfos[user].factor in ReferralStorage` inconsistent with `getUserTotalLocked(user)`, violating the invariant that a slot's penalty curve must not be resettable in a way that improves the exit terms of value already committed to cooldown and extracting High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: slot reuse through getNextAvailableUnlockSlot)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: getNextAvailableUnlockSlot() returns the first index whose amountInCoolDown == 0 once length reaches maxSlot, and cancelUnlock/forceUnLock both zero that field, so startUnlock overwrites startTime and endTime of a reused slot and resets the penalty curve. Precondition: the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor.
- Invariant to test: a slot's penalty curve must not be resettable in a way that improves the exit terms of value already committed to cooldown; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `startUnlock(uint256 _amountToCoolDown)` sequence atomically under the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, asserting at the end that `userInfos[user].factor in ReferralStorage` still equals `getUserTotalLocked(user)` and the PoC's balance delta is non-positive.
