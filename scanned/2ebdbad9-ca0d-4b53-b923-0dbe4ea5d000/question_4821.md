# Q4821: VLMGP.unlock - forceUnLock never refreshes the referral boost factor

## Question
Consider VLMGP.sol, where startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Assuming the attacker repeats cancelUnlock and startUnlock inside a single transaction, can an unprivileged attacker turn this into a divergence between `maxSlot` and `userUnlockings[user].length` via `unlock(uint256 _slotIndex)`, breaking the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance and producing High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: the attacker repeats cancelUnlock and startUnlock inside a single transaction.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `maxSlot` must stay reconciled with `userUnlockings[user].length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `unlock(uint256 _slotIndex)` sequence atomically under the attacker repeats cancelUnlock and startUnlock inside a single transaction, asserting at the end that `maxSlot` still equals `userUnlockings[user].length` and the PoC's balance delta is non-positive.
