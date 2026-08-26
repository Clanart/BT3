# Q3320: VLMGP.unlock - forceUnLock never refreshes the referral boost factor

## Question
In VLMGP.sol, startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Does `unlock(uint256 _slotIndex)` let an unprivileged caller exploit that under the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, so that `getRewardablePercentWAD(user)` diverges from `userUnlockings[user][i].amountInCoolDown`, the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `userUnlockings[user][i].amountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, then assert `getRewardablePercentWAD(user)` and `userUnlockings[user][i].amountInCoolDown` end identical in both runs.
