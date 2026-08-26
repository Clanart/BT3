# Q3668: VLMGP.unlock - forceUnLock never refreshes the referral boost factor

## Question
VLMGP.sol: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. With _slotIndex and how long after endTime the slot is redeemed under attacker control and the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, can an unprivileged caller sequence `unlock(uint256 _slotIndex)` so that `userUnlockings[user][i].endTime` and `block.timestamp` no longer reconcile, violating the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance and realising High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `userUnlockings[user][i].endTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `unlock(uint256 _slotIndex)`: constrain the setup so that the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, fuzz the attacker inputs (_slotIndex and how long after endTime the slot is redeemed), and assert after every call that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance.
