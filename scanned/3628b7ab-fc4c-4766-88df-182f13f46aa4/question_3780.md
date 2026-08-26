# Q3780: VLMGP.forceUnLock - forceUnLock never refreshes the referral boost factor

## Question
In VLMGP.sol, startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Starting from a state where the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, can an unprivileged EOA use `forceUnLock(uint256 _slotIndex)` to leave `userInfos[user].factor in ReferralStorage` inconsistent with `getUserTotalLocked(user)`, violating the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance and extracting High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `forceUnLock(uint256 _slotIndex)`: constrain the setup so that the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, fuzz the attacker inputs (_slotIndex and the exact point inside the cooldown curve at which the penalty is priced), and assert after every call that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance.
