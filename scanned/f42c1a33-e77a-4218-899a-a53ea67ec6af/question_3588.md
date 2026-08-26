# Q3588: VLMGP.startUnlock - forceUnLock never refreshes the referral boost factor

## Question
In VLMGP.sol, startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Can an unprivileged attacker reach this through `startUnlock(uint256 _amountToCoolDown)` while the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, and drive `getRewardablePercentWAD(user)` out of agreement with `userUnlockings[user][i].amountInCoolDown` - breaking the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance - for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `userUnlockings[user][i].amountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountToCoolDown and the timestamp that fixes startTime/endTime for the slot) under the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, asserting on every row that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance.
