# Q3011: VLMGP.cancelUnlock - forceUnLock never refreshes the referral boost factor

## Question
VLMGP.sol: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. With _slotIndex and the moment the cooldown is aborted under attacker control and the pool the attacker voted for has since been deactivated so unvote reverts, can an unprivileged caller sequence `cancelUnlock(uint256 _slotIndex)` so that `getRewardablePercentWAD(user)` and `userUnlockings[user][i].amountInCoolDown` no longer reconcile, violating the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance and realising High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: the pool the attacker voted for has since been deactivated so unvote reverts.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `userUnlockings[user][i].amountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool the attacker voted for has since been deactivated so unvote reverts, call `cancelUnlock(uint256 _slotIndex)`, and assert `getRewardablePercentWAD(user)` equals `userUnlockings[user][i].amountInCoolDown` and that no account can withdraw more than it put in.
