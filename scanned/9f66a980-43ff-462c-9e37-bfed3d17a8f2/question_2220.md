# Q2220: VLMGP.forceUnLock - forceUnLock never refreshes the referral boost factor

## Question
In VLMGP.sol, startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Starting from a state where the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, can an unprivileged EOA use `forceUnLock(uint256 _slotIndex)` to leave `totalAmount` inconsistent with `sum of userInfo[vlmgp][*].amount in MasterMagpie`, violating the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance and extracting High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `totalAmount` must stay reconciled with `sum of userInfo[vlmgp][*].amount in MasterMagpie`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_slotIndex and the exact point inside the cooldown curve at which the penalty is priced) under the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, asserting on every row that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance.
