# Q2128: VLMGP.cancelUnlock - forceUnLock never refreshes the referral boost factor

## Question
Note that in VLMGP.sol, startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Can an attacker holding only tokens bought on market reach it via `cancelUnlock(uint256 _slotIndex)` under the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one and force `getUserAmountInCoolDown(user)` apart from `totalAmountInCoolDown`, breaking the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, have the attacker run `cancelUnlock(uint256 _slotIndex)`, then assert the victim's claimable value and the `getUserAmountInCoolDown(user)` versus `totalAmountInCoolDown` relation are unchanged by the attacker's transaction.
