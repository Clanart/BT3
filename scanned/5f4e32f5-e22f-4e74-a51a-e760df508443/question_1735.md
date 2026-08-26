# Q1735: VLMGP.forceUnLock - forceUnLock never refreshes the referral boost factor

## Question
Note that in VLMGP.sol, startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Can an attacker holding only tokens bought on market reach it via `forceUnLock(uint256 _slotIndex)` under coolDownInSecs is at its configured production value and endTime is far in the future and force `getUserAmountInCoolDown(user)` apart from `totalAmountInCoolDown`, breaking the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: coolDownInSecs is at its configured production value and endTime is far in the future.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under coolDownInSecs is at its configured production value and endTime is far in the future, then assert `getUserAmountInCoolDown(user)` and `totalAmountInCoolDown` end identical in both runs.
