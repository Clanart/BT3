# Q3439: VLMGP.forceUnLock - forceUnLock never refreshes the referral boost factor

## Question
Note that in VLMGP.sol, startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Can an attacker holding only tokens bought on market reach it via `forceUnLock(uint256 _slotIndex)` under the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard and force `totalPenalty` apart from `IERC20(MGP).balanceOf(address(this))`, breaking the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `totalPenalty` must stay reconciled with `IERC20(MGP).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, then assert `totalPenalty` and `IERC20(MGP).balanceOf(address(this))` end identical in both runs.
