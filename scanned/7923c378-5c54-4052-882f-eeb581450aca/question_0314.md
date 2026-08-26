# Q0314: VLMGP.unlock - forceUnLock never refreshes the referral boost factor

## Question
VLMGP.sol: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Under the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, is there an unprivileged sequence of `unlock(uint256 _slotIndex)` that leaves `userInfos[user].factor in ReferralStorage` unreconciled with `getUserTotalLocked(user)`, violates the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance, and delivers High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, call `unlock(uint256 _slotIndex)`, and assert `userInfos[user].factor in ReferralStorage` equals `getUserTotalLocked(user)` and that no account can withdraw more than it put in.
