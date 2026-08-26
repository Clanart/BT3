# Q0810: VLMGP.startUnlock - forceUnLock never refreshes the referral boost factor

## Question
Note that in VLMGP.sol, startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Can an attacker holding only tokens bought on market reach it via `startUnlock(uint256 _amountToCoolDown)` under the attacker's slot matured exactly one second ago and force `userInfos[user].factor in ReferralStorage` apart from `getUserTotalLocked(user)`, breaking the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: the attacker's slot matured exactly one second ago.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker's slot matured exactly one second ago, call `startUnlock(uint256 _amountToCoolDown)`, and assert `userInfos[user].factor in ReferralStorage` equals `getUserTotalLocked(user)` and that no account can withdraw more than it put in.
