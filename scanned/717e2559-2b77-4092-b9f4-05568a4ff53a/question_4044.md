# Q4044: VLMGP.cancelUnlock - forceUnLock never refreshes the referral boost factor

## Question
Consider VLMGP.sol, where startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Assuming a large vesting MGP distribution has just been queued into the vlMGP rewarder, can an unprivileged attacker turn this into a divergence between `userInfos[user].factor in ReferralStorage` and `getUserTotalLocked(user)` via `cancelUnlock(uint256 _slotIndex)`, breaking the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance and producing High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: a large vesting MGP distribution has just been queued into the vlMGP rewarder.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large vesting MGP distribution has just been queued into the vlMGP rewarder, call `cancelUnlock(uint256 _slotIndex)`, and assert `userInfos[user].factor in ReferralStorage` equals `getUserTotalLocked(user)` and that no account can withdraw more than it put in.
