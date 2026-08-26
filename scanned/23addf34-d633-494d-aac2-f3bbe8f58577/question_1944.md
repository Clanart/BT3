# Q1944: VLMGP.startUnlock - forceUnLock never refreshes the referral boost factor

## Question
VLMGP.sol - startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Can an unprivileged attacker controlling _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot, under the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, exploit this through `startUnlock(uint256 _amountToCoolDown)` to break the reconciliation between `maxSlot` and `userUnlockings[user].length` and the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance, yielding High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `maxSlot` must stay reconciled with `userUnlockings[user].length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, then assert `maxSlot` and `userUnlockings[user].length` end identical in both runs.
