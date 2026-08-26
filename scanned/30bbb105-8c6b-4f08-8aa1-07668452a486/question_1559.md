# Q1559: VLMGP.unlock - forceUnLock never refreshes the referral boost factor

## Question
VLMGP.sol - startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Can an unprivileged attacker controlling _slotIndex and how long after endTime the slot is redeemed, under coolDownInSecs is at its configured production value and endTime is far in the future, exploit this through `unlock(uint256 _slotIndex)` to break the reconciliation between `maxSlot` and `userUnlockings[user].length` and the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance, yielding High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: coolDownInSecs is at its configured production value and endTime is far in the future.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `maxSlot` must stay reconciled with `userUnlockings[user].length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `unlock(uint256 _slotIndex)`: constrain the setup so that coolDownInSecs is at its configured production value and endTime is far in the future, fuzz the attacker inputs (_slotIndex and how long after endTime the slot is redeemed), and assert after every call that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance.
