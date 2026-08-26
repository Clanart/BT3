# Q3999: VLMGP.unlock - forceUnLock never refreshes the referral boost factor

## Question
VLMGP.sol - startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Can an unprivileged attacker controlling _slotIndex and how long after endTime the slot is redeemed, under a large vesting MGP distribution has just been queued into the vlMGP rewarder, exploit this through `unlock(uint256 _slotIndex)` to break the reconciliation between `totalPenalty` and `IERC20(MGP).balanceOf(address(this))` and the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance, yielding High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: a large vesting MGP distribution has just been queued into the vlMGP rewarder.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `totalPenalty` must stay reconciled with `IERC20(MGP).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a large vesting MGP distribution has just been queued into the vlMGP rewarder, have the attacker run `unlock(uint256 _slotIndex)`, then assert the victim's claimable value and the `totalPenalty` versus `IERC20(MGP).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
